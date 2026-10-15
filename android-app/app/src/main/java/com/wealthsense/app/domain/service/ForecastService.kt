package com.wealthsense.app.domain.service

import android.content.Context
import android.util.Log
import com.wealthsense.app.data.database.dao.TransactionDao
import com.wealthsense.app.data.database.entity.TransactionEntity
import com.wealthsense.app.data.database.entity.TransactionType
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import org.tensorflow.lite.Interpreter
import java.io.FileInputStream
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import java.time.LocalDate
import java.time.temporal.ChronoUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.sqrt

/**
 * Result of a successful 7-day-ahead balance forecast.
 *
 * NOTE ON CONFIDENCE RANGE: this is NOT a statistically rigorous prediction
 * interval -- the model is a single point-estimate regressor with no
 * quantile/ensemble output. The range is the point prediction +/- the
 * model's own held-out TEST-SET MAE (see ml-training/evaluate_forecaster.py,
 * evaluated on 4,050 windows across 75 held-out synthetic users:
 * MAE = Rs 5,402.33 for the retrained, window-normalised model, see ml-training/retrain_window_norm.py; the earlier Rs 2,879.03 used leaky whole-series statistics). Treat it as an approximate display band, not a
 * formal confidence interval -- called out here so nothing downstream
 * mistakes it for one.
 */
data class ForecastResult(
    val predictedBalance: Double,
    val confidenceRangeLow: Double,
    val confidenceRangeHigh: Double
)

/**
 * Wraps [ForecastResult] so the cold-start case (a new user with under
 * [ForecastService.WINDOW_DAYS] days of history) is a real, typed state the
 * UI can switch on -- not an exception, not a sentinel value smuggled into
 * ForecastResult itself.
 */
sealed interface ForecastOutcome {
    data class Success(val result: ForecastResult) : ForecastOutcome
    data class NotEnoughData(
        val daysAvailable: Int,
        val daysRequired: Int = ForecastService.WINDOW_DAYS
    ) : ForecastOutcome
    data class Error(val message: String) : ForecastOutcome
}

/**
 * Predicts the user's balance [HORIZON_DAYS] days ahead, using the
 * Bidirectional LSTM forecaster trained in ml-training/train_forecaster.py
 * and exported to TFLite in ml-training/export_forecaster_tflite.py.
 *
 * Feature engineering here MUST mirror train_forecaster.py's
 * build_daily_balance_series() / build_windows() exactly, or predictions
 * silently break (the model still runs, it just runs on out-of-distribution
 * input and returns garbage with no error). See the inline comments at each
 * step below for the specific correspondence.
 *
 * Wired into Hilt the same way RuleEngine.kt is: a plain @Singleton class
 * with an @Inject constructor, no separate interface/impl split and no
 * explicit @Module -- Hilt satisfies this via constructor injection alone.
 */
@Singleton
class ForecastService @Inject constructor(
    @ApplicationContext private val context: Context,
    private val transactionDao: TransactionDao
) {

    companion object {
        private const val TAG = "ForecastService"

        /** Trailing history window the model expects, in days. Must match
         *  train_forecaster.py's WINDOW_SIZE exactly. */
        const val WINDOW_DAYS = 30

        /** How far ahead the model predicts, in days. Must match
         *  train_forecaster.py's HORIZON exactly. */
        private const val HORIZON_DAYS = 7

        private const val MODEL_ASSET_NAME = "forecaster.tflite"

        /** From ml-training/evaluate_forecaster.py's held-out test-set
         *  evaluation. See ForecastResult's doc comment for how this is used. */
        private const val TEST_MAE_RUPEES = 5402.33
    }

    @Volatile
    private var interpreter: Interpreter? = null

    /**
     * Runs a forecast using the current user's transaction history. Never
     * throws -- I/O errors, a missing/corrupt model asset, or insufficient
     * history all come back as a typed [ForecastOutcome], not an exception.
     */
    suspend fun getForecast(): ForecastOutcome = withContext(Dispatchers.IO) {
        try {
            val today = LocalDate.now()
            val windowStart = today.minusDays((WINDOW_DAYS - 1).toLong())

            // Single query for the user's full transaction history (existing
            // TransactionDao method, DESC-ordered by date, is_deleted=0
            // already filtered -- no parallel data path, no new DAO method).
            val allHistory = transactionDao.getAllTransactions().first()

            val earliestDate = allHistory.minOfOrNull { it.dateTime.toLocalDate() }

            // Cold-start check: if the user's earliest-ever transaction is
            // more recent than windowStart, the early part of a 30-day window
            // would be artificially flat (zero net flow) not because nothing
            // happened, but because the account/app didn't exist yet -- a
            // materially different signal than training, where every
            // synthetic user had a full, genuine 90-day history. Don't feed
            // the model a window it was never trained to see.
            if (earliestDate == null || earliestDate.isAfter(windowStart)) {
                val daysAvailable = if (earliestDate == null) {
                    0
                } else {
                    (ChronoUnit.DAYS.between(earliestDate, today) + 1).toInt().coerceAtMost(WINDOW_DAYS)
                }
                return@withContext ForecastOutcome.NotEnoughData(daysAvailable = daysAvailable)
            }

            val windowTransactions = allHistory.filter {
                !it.dateTime.toLocalDate().isBefore(windowStart)
            }

            // --- Build the dense 30-day daily net-flow series, mirroring
            // train_forecaster.py's build_daily_balance_series(): one entry
            // per calendar day, days with zero transactions filled with 0.0
            // net flow (same as the dense (user, date) grid reindex there). ---
            val dailyNetFlow = DoubleArray(WINDOW_DAYS)
            for (txn in windowTransactions) {
                val dayIndex = ChronoUnit.DAYS.between(windowStart, txn.dateTime.toLocalDate()).toInt()
                if (dayIndex in 0 until WINDOW_DAYS) {
                    dailyNetFlow[dayIndex] += txn.amount.toDouble() * txn.netFlowSign()
                }
            }

            // Cumulative sum -> a RELATIVE balance series starting from 0 at
            // windowStart. This intentionally mirrors how the synthetic
            // training data's "balance" feature was built: cumsum of net flow
            // from day 0 of the simulation, NOT tied to any real starting
            // balance (see ml-training README's documented caveat on this).
            // It gets re-anchored to the user's real current balance below.
            val relativeBalance = DoubleArray(WINDOW_DAYS)
            var running = 0.0
            for (i in 0 until WINDOW_DAYS) {
                running += dailyNetFlow[i]
                relativeBalance[i] = running
            }

            // --- Normalization. Training normalized each synthetic user
            // against THAT USER'S OWN full 90-day history (see
            // forecaster_norm_stats.csv). A real, ongoing app user has no
            // equivalent "full future history" to normalize against at
            // inference time, so this normalizes against THIS SAME 30-day
            // window's own statistics instead -- the same underlying
            // principle (self-normalize per-entity), applied at the
            // granularity actually available on-device. This is a deliberate
            // adaptation, not an oversight.
            //
            // Two SEPARATE normalizations, exactly matching build_windows()
            // in train_forecaster.py: balance uses its own mean/std, net_flow
            // uses its own (different) mean/std. ---
            val balMean = relativeBalance.average()
            val balStd = relativeBalance.stdDevOrEpsilon()
            val flowMean = dailyNetFlow.average()
            val flowStd = dailyNetFlow.stdDevOrEpsilon()

            val input = Array(1) { Array(WINDOW_DAYS) { FloatArray(2) } }
            for (i in 0 until WINDOW_DAYS) {
                input[0][i][0] = ((relativeBalance[i] - balMean) / balStd).toFloat()
                input[0][i][1] = ((dailyNetFlow[i] - flowMean) / flowStd).toFloat()
            }

            val interp = getOrLoadInterpreter()
            val output = Array(1) { FloatArray(1) }
            interp.run(input, output)
            val predictedNorm = output[0][0]

            // De-normalize using the BALANCE mean/std (the model's target was
            // trained in that same normalized space).
            val predictedRelativeBalance = predictedNorm * balStd + balMean

            // --- Re-anchor to the user's real current balance. The value
            // above is still in the "relative to windowStart" frame -- it's
            // really predicting a net-flow-over-the-next-7-days delta, not an
            // absolute rupee figure. Anchor it to the most recent REAL
            // balance on record, sourced from the transaction stream itself
            // via balanceAfter (populated when an SMS reports one) -- still
            // TransactionDao only, no parallel data path. ---
            val currentRealBalance = findMostRecentKnownBalance(allHistory)
            if (currentRealBalance == null) {
                Log.w(TAG, "No transaction in history has a known balanceAfter -- " +
                    "forecast is anchored to Rs 0 and represents a relative change only.")
            }
            val anchor = currentRealBalance ?: 0.0

            val predictedDelta = predictedRelativeBalance - relativeBalance.last()
            val predictedAbsoluteBalance = anchor + predictedDelta

            ForecastOutcome.Success(
                ForecastResult(
                    predictedBalance = predictedAbsoluteBalance,
                    confidenceRangeLow = predictedAbsoluteBalance - TEST_MAE_RUPEES,
                    confidenceRangeHigh = predictedAbsoluteBalance + TEST_MAE_RUPEES
                )
            )
        } catch (e: Exception) {
            Log.e(TAG, "Forecast inference failed", e)
            ForecastOutcome.Error(e.message ?: "Unknown error running forecast")
        }
    }

    /** allHistory is DESC-ordered by date_time (per getAllTransactions()'s
     *  query), so the first entry with a non-null balanceAfter IS the most
     *  recent known real balance. */
    private fun findMostRecentKnownBalance(allHistoryDesc: List<TransactionEntity>): Double? =
        allHistoryDesc.firstOrNull { it.balanceAfter != null }?.balanceAfter?.toDouble()

    private fun DoubleArray.stdDevOrEpsilon(): Double {
        val mean = average()
        val variance = sumOf { (it - mean) * (it - mean) } / size
        return sqrt(variance).coerceAtLeast(1e-6)
    }

    @Synchronized
    private fun getOrLoadInterpreter(): Interpreter {
        interpreter?.let { return it }
        val newInterpreter = Interpreter(loadModelFile())
        interpreter = newInterpreter
        return newInterpreter
    }

    private fun loadModelFile(): MappedByteBuffer {
        val assetFileDescriptor = context.assets.openFd(MODEL_ASSET_NAME)
        return FileInputStream(assetFileDescriptor.fileDescriptor).use { inputStream ->
            val fileChannel = inputStream.channel
            fileChannel.map(
                FileChannel.MapMode.READ_ONLY,
                assetFileDescriptor.startOffset,
                assetFileDescriptor.declaredLength
            )
        }
    }
}

/**
 * Sign convention for AGGREGATE net-worth impact, sourced directly from
 * this app's own existing convention in
 * TransactionDetailViewModel.applyBalanceEffect() / revertBalanceEffect():
 *   INCOME -> credit, EXPENSE / INVESTMENT -> debit, CREDIT -> no balance
 *   effect ("No balance update for now", per that file).
 *
 * TRANSFER deliberately diverges from that file's convention: there, a
 * transfer subtracts from the source account (correct for a PER-ACCOUNT
 * balance). Here we're forecasting one AGGREGATE balance across all the
 * user's accounts, where a transfer between two accounts they own nets to
 * zero -- the money didn't leave their total balance. This also matches how
 * the synthetic training data was generated (no TRANSFER category existed
 * there at all).
 */
private fun TransactionEntity.netFlowSign(): Int = when (transactionType) {
    TransactionType.INCOME -> 1
    TransactionType.EXPENSE, TransactionType.INVESTMENT -> -1
    TransactionType.CREDIT, TransactionType.TRANSFER -> 0
}
