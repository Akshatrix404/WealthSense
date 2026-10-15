package com.wealthsense.app.domain.service

import android.content.Context
import android.util.Log
import com.wealthsense.app.data.database.dao.TransactionDao
import com.wealthsense.app.data.database.entity.TransactionEntity
import dagger.hilt.android.qualifiers.ApplicationContext
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import java.nio.FloatBuffer
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.sqrt

/**
 * Result of scoring a single transaction for anomalousness.
 */
data class AnomalyScore(
    val isAnomalous: Boolean,
    /** Raw decision_function-style score from Isolation Forest -- more
     *  negative means more anomalous. Exposed for logging/debugging, not
     *  meant to be shown to the user directly (no calibrated meaning
     *  outside relative comparison). */
    val rawScore: Float
)

/**
 * Scores a transaction for anomalousness using the Isolation Forest
 * trained in ml-training/train_anomaly_detector.py and exported to ONNX
 * in ml-training/export_anomaly_detector_onnx.py.
 *
 * FEATURE COMPUTATION MUST MATCH TRAINING EXACTLY, same principle as
 * ForecastService.kt: the model uses exactly two features,
 *   - amount (raw rupees)
 *   - amount_zscore: how many std-devs this amount is from THIS user's
 *     own historical mean for THIS category, using STRICTLY PRIOR
 *     transactions only (no leakage -- the current transaction isn't
 *     included in its own baseline)
 * (category, day-of-week, and category-frequency were all tried during
 * training and empirically REDUCED performance -- see
 * ml-training/README.md for the ablation results. They are deliberately
 * not computed here.)
 *
 * Wired into Hilt the same way ForecastService and RuleEngine are: a
 * plain @Singleton class with an @Inject constructor, no separate
 * interface/impl split, no explicit @Module.
 */
@Singleton
class AnomalyDetectionService @Inject constructor(
    @ApplicationContext private val context: Context,
    private val transactionDao: TransactionDao
) {
    companion object {
        private const val TAG = "AnomalyDetectionService"
        private const val MODEL_ASSET_NAME = "isolation_forest.onnx"
    }

    @Volatile
    private var session: OrtSession? = null
    private val ortEnvironment: OrtEnvironment by lazy { OrtEnvironment.getEnvironment() }

    /**
     * Scores a single transaction. Returns null (not a crash) if the
     * model can't be loaded or inference fails for any reason -- a
     * scoring failure should never block saving the transaction itself,
     * since anomaly detection is a secondary, best-effort feature.
     */
    suspend fun score(transaction: TransactionEntity): AnomalyScore? = withContext(Dispatchers.IO) {
        try {
            val amount = transaction.amount.toFloat()
            val amountZscore = computeAmountZscore(transaction)

            val sess = getOrLoadSession()
            val inputName = sess.inputNames.iterator().next()

            val inputBuffer = FloatBuffer.wrap(floatArrayOf(amount, amountZscore))
            val inputTensor = OnnxTensor.createTensor(ortEnvironment, inputBuffer, longArrayOf(1, 2))

            inputTensor.use { tensor ->
                sess.run(mapOf(inputName to tensor)).use { results ->
                    // skl2onnx's IsolationForest converter (with
                    // score_samples=True, see export script) produces
                    // named outputs: "label" (1 normal / -1 anomaly, as
                    // int64), "scores", "score_samples". Access by name,
                    // not position -- OrtSession.Result has no positional
                    // indexing.
                    val labelOnnxValue = results.get("label").orElse(null)
                        ?: error("ONNX model has no 'label' output -- check the export")
                    val label: Long = extractLongScalar(labelOnnxValue.value)

                    val scoreOnnxValue = results.get("scores").orElse(null)
                    val rawScore: Float = scoreOnnxValue?.value?.let { extractFloatScalar(it) } ?: 0f

                    AnomalyScore(isAnomalous = label == -1L, rawScore = rawScore)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Anomaly scoring failed for transaction ${transaction.id}", e)
            null
        }
    }

    /** ONNX Runtime's Java API returns tensor values as nested Java arrays
     *  whose depth matches the tensor's rank -- a [1] shaped int64 tensor
     *  comes back as a bare LongArray, a [1,1] shaped one as
     *  Array<LongArray>. Handles both without assuming which shape
     *  skl2onnx chose, since that wasn't verified by running real
     *  inference (only sklearn-vs-ONNX prediction parity was verified in
     *  Python, see export_anomaly_detector_onnx.py). */
    private fun extractLongScalar(value: Any?): Long = when (value) {
        is LongArray -> value[0]
        is Array<*> -> extractLongScalar(value[0])
        is Long -> value
        else -> {
            Log.w(TAG, "Unexpected ONNX label output type: ${value?.javaClass}, defaulting to normal")
            1L
        }
    }

    private fun extractFloatScalar(value: Any): Float = when (value) {
        is FloatArray -> value[0]
        is Array<*> -> extractFloatScalar(value[0] as Any)
        is Float -> value
        is Double -> value.toFloat()
        else -> 0f
    }

    /**
     * (amount - mean) / std, using this user's own transactions in the
     * same category, strictly before this transaction's date/time.
     * Mirrors train_anomaly_detector.py's build_features() exactly:
     * fewer than 2 prior transactions in this category -> neutral 0.0
     * rather than a misleading extreme value; std floored at 1e-6 to
     * avoid divide-by-zero, matching the training script's guard.
     */
    private suspend fun computeAmountZscore(transaction: TransactionEntity): Float {
        // Reuses the existing TransactionDao only -- no parallel data path.
        // getAllTransactions() is DESC-ordered; we filter to strictly-prior,
        // same-category transactions in memory.
        val allHistory = transactionDao.getAllTransactions()
        val history = try {
            allHistory.first()
        } catch (e: Exception) {
            Log.w(TAG, "Could not read transaction history for z-score, defaulting to 0", e)
            return 0f
        }

        val priorSameCategory = history.filter {
            it.category == transaction.category &&
                it.dateTime.isBefore(transaction.dateTime) &&
                it.id != transaction.id
        }

        if (priorSameCategory.size < 2) return 0f

        val amounts = priorSameCategory.map { it.amount.toDouble() }
        val mean = amounts.average()
        val variance = amounts.sumOf { (it - mean) * (it - mean) } / amounts.size
        val std = sqrt(variance).coerceAtLeast(1e-6)

        return ((transaction.amount.toDouble() - mean) / std).toFloat()
    }

    @Synchronized
    private fun getOrLoadSession(): OrtSession {
        session?.let { return it }
        val modelBytes = context.assets.open(MODEL_ASSET_NAME).use { it.readBytes() }
        val newSession = ortEnvironment.createSession(modelBytes)
        session = newSession
        return newSession
    }
}
