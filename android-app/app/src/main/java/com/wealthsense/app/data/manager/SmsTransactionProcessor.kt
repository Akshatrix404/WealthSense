package com.wealthsense.app.data.manager

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.wealthsense.app.worker.ChainAnchorWorker
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.TimeUnit
import com.ritesh.parser.core.ParsedTransaction
import com.ritesh.parser.core.bank.BankParserFactory
import com.wealthsense.app.data.database.entity.AccountBalanceEntity
import com.wealthsense.app.data.database.entity.CardType
import com.wealthsense.app.data.database.entity.TransactionEntity
import com.wealthsense.app.data.database.entity.TransactionType
import com.wealthsense.app.data.mapper.toEntity
import com.wealthsense.app.data.mapper.toEntityType
import com.wealthsense.app.data.repository.AccountBalanceRepository
import com.wealthsense.app.data.repository.CardRepository
import com.wealthsense.app.data.repository.MerchantMappingRepository
import com.wealthsense.app.data.repository.SubscriptionRepository
import com.wealthsense.app.data.repository.TransactionRepository
import com.wealthsense.app.domain.repository.RuleRepository
import com.wealthsense.app.domain.service.AnomalyDetectionService
import com.wealthsense.app.domain.service.RuleEngine
import java.math.BigDecimal
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Shared processor for SMS transactions. Used by both SmsBroadcastReceiver
 * and OptimizedSmsReaderWorker to ensure consistent transaction processing.
 */
@Singleton
class SmsTransactionProcessor @Inject constructor(
    private val transactionRepository: TransactionRepository,
    private val accountBalanceRepository: AccountBalanceRepository,
    private val cardRepository: CardRepository,
    private val merchantMappingRepository: MerchantMappingRepository,
    private val subscriptionRepository: SubscriptionRepository,
    private val ruleRepository: RuleRepository,
    private val ruleEngine: RuleEngine,
    private val anomalyDetectionService: AnomalyDetectionService,
    private val notificationScheduler: NotificationScheduler,
    @ApplicationContext private val context: Context
) {
    companion object {
        private const val TAG = "SmsTransactionProcessor"
    }

    /**
     * Result of processing an SMS message
     */
    data class ProcessingResult(
        val success: Boolean,
        val transactionId: Long? = null,
        val reason: String? = null
    )

    /**
     * Parses and saves a transaction from an SMS message.
     *
     * @param sender SMS sender address
     * @param body SMS body text
     * @param timestamp SMS timestamp in milliseconds
     * @return ProcessingResult indicating success/failure and transaction ID
     */
    suspend fun processAndSaveTransaction(
        sender: String,
        body: String,
        timestamp: Long
    ): ProcessingResult {
        try {
            // Get the appropriate parser for this sender
            val parser = BankParserFactory.getParser(sender) ?: return ProcessingResult(
                false,
                reason = "No parser found for sender: $sender"
            )

            // Parse the SMS
            val parsedTransaction =
                parser.parse(body, sender, timestamp) ?: return ProcessingResult(
                    false,
                    reason = "Could not parse transaction from SMS"
                )

            Log.d(TAG, "Parsed transaction: ${parsedTransaction.amount} from ${parsedTransaction.bankName}")

            // Save the transaction
            return saveParsedTransaction(parsedTransaction, body)
        } catch (e: Exception) {
            Log.e(TAG, "Error processing SMS", e)
            return ProcessingResult(false, reason = e.message)
        }
    }

    /**
     * Saves a parsed transaction to the database with all necessary processing:
     * - Duplicate detection
     * - Merchant mapping
     * - Rule application
     * - Subscription matching
     * - Balance updates
     */
    suspend fun saveParsedTransaction(
        parsedTransaction: ParsedTransaction,
        smsBody: String
    ): ProcessingResult {
        return try {
            // Convert to entity
            val entity = parsedTransaction.toEntity()

            // Check if this transaction was previously deleted by the user
            val existingTransaction = transactionRepository.getTransactionByHash(entity.transactionHash)
            if (existingTransaction != null) {
                if (existingTransaction.isDeleted) {
                    Log.d(TAG, "Skipping previously deleted transaction with hash: ${entity.transactionHash}")
                    return ProcessingResult(false, reason = "Transaction was previously deleted")
                }
                // Transaction already exists and not deleted - normal deduplication
                Log.d(TAG, "Transaction already exists: ${entity.transactionHash}")
                return ProcessingResult(false, reason = "Duplicate transaction")
            }

            // Check for custom merchant mapping
            val customCategory = merchantMappingRepository.getCategoryForMerchant(entity.merchantName)
            val entityWithMapping = if (customCategory != null) {
                Log.d(TAG, "Found custom category mapping: ${entity.merchantName} -> $customCategory")
                entity.copy(category = customCategory)
            } else {
                entity
            }

            // Apply rule engine to the transaction
            val activeRules = ruleRepository.getActiveRulesByType(entityWithMapping.transactionType)

            // Check if this transaction should be blocked
            val blockingRule = ruleEngine.shouldBlockTransaction(
                entityWithMapping,
                smsBody,
                activeRules
            )

            if (blockingRule != null) {
                Log.d(TAG, "Transaction blocked by rule: ${blockingRule.name}")
                return ProcessingResult(false, reason = "Blocked by rule: ${blockingRule.name}")
            }

            val (entityWithRules, ruleApplications) = ruleEngine.evaluateRules(
                entityWithMapping,
                smsBody,
                activeRules
            )

            if (ruleApplications.isNotEmpty()) {
                Log.d(TAG, "Applied ${ruleApplications.size} rules to transaction")
            }

            // Check if this transaction matches an active subscription
            val matchedSubscription = subscriptionRepository.matchTransactionToSubscription(
                entityWithRules.merchantName,
                entityWithRules.amount
            )

            val finalEntity = if (matchedSubscription != null) {
                Log.d(TAG, "Transaction matched to active subscription: ${matchedSubscription.merchantName}")
                subscriptionRepository.updateNextPaymentDateAfterCharge(
                    matchedSubscription.id,
                    entityWithRules.dateTime.toLocalDate()
                )
                entityWithRules.copy(isRecurring = true)
            } else {
                entityWithRules
            }

            val rowId = transactionRepository.insertTransaction(finalEntity)
            if (rowId != -1L) {
                Log.d(TAG, "Saved new transaction with ID: $rowId${if (finalEntity.isRecurring) " (Recurring)" else ""}")

                // Save rule applications if any rules were applied
                if (ruleApplications.isNotEmpty()) {
                    val applicationsWithId = ruleApplications.map { 
                        it.copy(transactionId = rowId.toString())
                    }
                    ruleRepository.saveRuleApplications(applicationsWithId)
                }

                // Process balance updates
                processBalanceUpdate(parsedTransaction, finalEntity, rowId)

                // Anomaly detection: score every newly saved SMS-parsed
                // transaction immediately. Best-effort -- a scoring
                // failure or a flagged-but-unnotifiable transaction
                // never blocks the save itself (already committed above).
                checkForAnomalyAndNotify(finalEntity.copy(id = rowId))

                // On-chain anchoring: SMS-parsed transactions are this
                // app's PRIMARY transaction source, so anchoring only
                // manually-added ones (the original Prompt 6 scope, via
                // AddTransactionUseCase.kt) would leave most real usage
                // unanchored. Same fire-and-forget WorkManager approach,
                // same worker class, same offline-first guarantee.
                enqueueChainAnchor(rowId)

                return ProcessingResult(true, transactionId = rowId)
            } else {
                Log.d(TAG, "Transaction already exists (duplicate): ${entity.transactionHash}")
                return ProcessingResult(false, reason = "Duplicate transaction")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error saving transaction: ${e.message}")
            return ProcessingResult(false, reason = e.message)
        }
    }

    /**
     * Scores a just-saved transaction with the Isolation Forest
     * (AnomalyDetectionService) and, if flagged, shows an immediate
     * notification. Wrapped in try/catch so this best-effort feature can
     * never crash the SMS-processing pipeline or prevent a transaction
     * from being saved -- the transaction is already committed by the
     * time this runs.
     */
    private fun enqueueChainAnchor(transactionId: Long) {
        try {
            val anchorRequest = OneTimeWorkRequestBuilder<ChainAnchorWorker>()
                .setInputData(ChainAnchorWorker.inputData(transactionId))
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()

            WorkManager.getInstance(context).enqueueUniqueWork(
                "chain_anchor_$transactionId",
                ExistingWorkPolicy.KEEP,
                anchorRequest
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to enqueue chain anchor for transaction $transactionId", e)
        }
    }

    private suspend fun checkForAnomalyAndNotify(entity: TransactionEntity) {
        try {
            val result = anomalyDetectionService.score(entity) ?: return
            if (result.isAnomalous) {
                Log.d(TAG, "Anomaly flagged: ${entity.merchantName}, amount=${entity.amount}, " +
                    "rawScore=${result.rawScore}")
                notificationScheduler.showAnomalyAlert(entity)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Anomaly check failed for transaction ${entity.id}", e)
        }
    }

    private suspend fun processBalanceUpdate(
        parsedTransaction: ParsedTransaction,
        entity: TransactionEntity,
        rowId: Long
    ) {
        if (parsedTransaction.accountLast4 == null) return

        val isFromCard = parsedTransaction.isFromCard

        val targetAccountLast4: String? = if (isFromCard) {
            var card = parsedTransaction.accountLast4?.let {
                cardRepository.getCard(parsedTransaction.bankName, it)
            }

            if (card == null) {
                val isCredit = (parsedTransaction.type.toEntityType() == TransactionType.CREDIT)
                parsedTransaction.accountLast4?.let { accountLast4 ->
                    cardRepository.findOrCreateCard(
                        cardLast4 = accountLast4,
                        bankName = parsedTransaction.bankName,
                        isCredit = isCredit
                    )
                }
                card = parsedTransaction.accountLast4?.let {
                    cardRepository.getCard(parsedTransaction.bankName, it)
                }
            }

            if (card == null) {
                Log.w(TAG, "Could not create/find card for ${parsedTransaction.bankName}")
                null
            } else {
                // Update card's balance
                cardRepository.updateCardBalance(
                    cardId = card.id,
                    balance = parsedTransaction.balance,
                    source = parsedTransaction.smsBody.take(200),
                    date = LocalDateTime.ofInstant(
                        Instant.ofEpochMilli(parsedTransaction.timestamp),
                        ZoneId.systemDefault()
                    )
                )

                when {
                    card.cardType == CardType.CREDIT -> parsedTransaction.accountLast4
                    card.cardType == CardType.DEBIT && card.accountLast4 != null -> card.accountLast4
                    else -> null
                }
            }
        } else {
            parsedTransaction.accountLast4
        }

        if (targetAccountLast4 != null) {
            val isCreditCard = (parsedTransaction.type.toEntityType() == TransactionType.CREDIT) ||
                    parsedTransaction.accountLast4?.let {
                        cardRepository.getCard(parsedTransaction.bankName, it)?.cardType
                    } == CardType.CREDIT

            val existingAccount = accountBalanceRepository.getLatestBalance(
                parsedTransaction.bankName,
                targetAccountLast4
            )

            val newBalance = when {
                isCreditCard -> {
                    val currentBalance = existingAccount?.balance ?: BigDecimal.ZERO
                    currentBalance + parsedTransaction.amount
                }
                existingAccount?.isCreditCard == true && parsedTransaction.type.toEntityType() == TransactionType.INCOME -> {
                    val currentBalance = existingAccount.balance ?: BigDecimal.ZERO
                    (currentBalance - parsedTransaction.amount).max(BigDecimal.ZERO)
                }
                parsedTransaction.balance != null -> parsedTransaction.balance!!
                else -> {
                    // SMS doesn't have explicit balance - calculate based on transaction type
                    val currentBalance = existingAccount?.balance ?: BigDecimal.ZERO
                    when (parsedTransaction.type.toEntityType()) {
                        TransactionType.INCOME -> {
                            // Money coming in - add to balance
                            currentBalance + parsedTransaction.amount
                        }
                        TransactionType.EXPENSE, TransactionType.INVESTMENT -> {
                            // Money going out - subtract from balance
                            (currentBalance - parsedTransaction.amount).max(BigDecimal.ZERO)
                        }
                        TransactionType.CREDIT, TransactionType.TRANSFER -> {
                            // Keep existing balance for transfers (complex logic needed)
                            // Credit should be handled above, this is fallback
                            currentBalance
                        }
                    }
                }
            }

            val balanceEntity = AccountBalanceEntity(
                bankName = parsedTransaction.bankName,
                accountLast4 = targetAccountLast4,
                balance = newBalance,
                timestamp = entity.dateTime,
                transactionId = if (rowId != -1L) rowId else null,
                creditLimit = existingAccount?.creditLimit,
                isCreditCard = isCreditCard || (existingAccount?.isCreditCard ?: false),
                smsSource = parsedTransaction.smsBody.take(500),
                sourceType = "TRANSACTION",
                currency = parsedTransaction.currency
            )

            accountBalanceRepository.insertBalance(balanceEntity)
            Log.d(TAG, "Saved balance update for ${parsedTransaction.bankName} **$targetAccountLast4")
        }
    }
}
