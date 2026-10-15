package com.wealthsense.app.worker

import android.content.Context
import androidx.hilt.work.HiltWorker
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.WorkerParameters
import com.wealthsense.app.data.database.dao.TransactionDao
import com.wealthsense.app.data.network.AnchorRequest
import com.wealthsense.app.data.network.ChainAnchorApi
import com.wealthsense.app.data.network.CommitmentSigner
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import java.time.ZoneOffset

/**
 * Fire-and-forget on-chain anchoring for a single transaction. Runs via
 * WorkManager rather than a blocking network call for exactly the reason
 * WealthSense is offline-first: a transaction must save and be usable
 * immediately regardless of network state, and anchoring is a
 * secondary, best-effort enhancement that can happen whenever
 * connectivity allows -- including much later, or (if it never becomes
 * available) not at all, which is an acceptable degraded state, not a
 * failure state for the app's core purpose.
 *
 * On success, writes chainTxHash/chainExplorerUrl back onto the
 * transaction row so the UI (see the on-chain badge Composable) can
 * show it. On failure, WorkManager's retry policy (set by the caller,
 * see AddTransactionUseCase.kt) handles backoff -- this worker itself
 * doesn't decide whether to give up, just reports Result.retry() on
 * recoverable failure.
 */
@HiltWorker
class ChainAnchorWorker @AssistedInject constructor(
    @Assisted appContext: Context,
    @Assisted workerParams: WorkerParameters,
    private val chainAnchorApi: ChainAnchorApi,
    private val transactionDao: TransactionDao
) : CoroutineWorker(appContext, workerParams) {

    override suspend fun doWork(): Result {
        val transactionId = inputData.getLong(KEY_TRANSACTION_ID, -1L)
        if (transactionId == -1L) {
            return Result.failure() // malformed input, no point retrying
        }

        val transaction = transactionDao.getTransactionById(transactionId)
            ?: return Result.failure() // transaction was deleted before this ran

        // Already anchored (e.g. a retry racing a previous success) --
        // nothing to do.
        if (transaction.chainTxHash != null) {
            return Result.success()
        }

        return try {
            // The commitment is computed on the phone with a hardware-backed key, so the
            // bridge and the chain never see the amount, category, merchant or time.
            val request = AnchorRequest(
                commitment = CommitmentSigner.commit(
                    amountPaise = transaction.amount.movePointRight(2).toLong(),
                    category = transaction.category,
                    epochSec = transaction.dateTime.toEpochSecond(ZoneOffset.UTC),
                    merchant = transaction.merchantName,
                    txType = transaction.transactionType.name
                )
            )

            val response = chainAnchorApi.anchorTransaction(request)

            transactionDao.updateTransaction(
                transaction.copy(
                    chainTxHash = response.tx_hash,
                    chainExplorerUrl = response.explorer_url
                )
            )

            Result.success()
        } catch (e: Exception) {
            // Network unavailable, backend down, chain call failed, etc.
            // -- all recoverable, let WorkManager's backoff policy retry.
            if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.failure()
        }
    }

    companion object {
        private const val KEY_TRANSACTION_ID = "transaction_id"
        private const val MAX_ATTEMPTS = 5

        fun inputData(transactionId: Long): Data {
            return Data.Builder()
                .putLong(KEY_TRANSACTION_ID, transactionId)
                .build()
        }
    }
}
