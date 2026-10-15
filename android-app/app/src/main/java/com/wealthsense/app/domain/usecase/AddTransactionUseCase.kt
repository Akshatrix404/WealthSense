package com.wealthsense.app.domain.usecase

import com.wealthsense.app.utils.SubscriptionUtils

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.wealthsense.app.data.database.entity.AccountBalanceEntity
import com.wealthsense.app.data.database.entity.SubscriptionEntity
import com.wealthsense.app.data.database.entity.SubscriptionState
import com.wealthsense.app.data.database.entity.TransactionEntity
import com.wealthsense.app.data.database.entity.TransactionType
import com.wealthsense.app.data.repository.AccountBalanceRepository
import com.wealthsense.app.data.repository.SubscriptionRepository
import com.wealthsense.app.data.repository.TransactionRepository
import com.wealthsense.app.worker.ChainAnchorWorker
import dagger.hilt.android.qualifiers.ApplicationContext
import java.math.BigDecimal
import java.security.MessageDigest
import java.time.LocalDate
import java.time.LocalDateTime
import java.util.concurrent.TimeUnit
import javax.inject.Inject

class AddTransactionUseCase
@Inject
constructor(
        private val transactionRepository: TransactionRepository,
        private val subscriptionRepository: SubscriptionRepository,
        private val accountBalanceRepository: AccountBalanceRepository,
        @ApplicationContext private val context: Context
) {
    suspend fun execute(
            amount: BigDecimal,
            merchant: String,
            category: String,
            type: TransactionType,
            date: LocalDateTime,
            notes: String? = null,
            subcategory: String? = null,
            isRecurring: Boolean = false,
            bankName: String? = null,
            accountLast4: String? = null,
            currency: String = "INR",
            sourceAccountId: Long? = null,
            targetAccountBankName: String? = null,
            targetAccountLast4: String? = null,
            billingCycle: String? = null,
            createSubscription: Boolean = true,
            attachments: String = ""
    ) {
        // Generate a unique hash for manual transactions
        val transactionHash =
                generateManualTransactionHash(amount = amount, merchant = merchant, date = date)

        // Create the transaction entity
        val transaction =
                TransactionEntity(
                        amount = amount,
                        merchantName = merchant,
                        category = category,
                        subcategory = subcategory,
                        transactionType = type,
                        dateTime = date,
                        description = notes,
                        smsBody = null, // null indicates manual entry
                        bankName = bankName ?: "Manual Entry",
                        smsSender = null, // null indicates manual entry
                        accountNumber = accountLast4,
                        balanceAfter = null,
                        transactionHash = transactionHash,
                        isRecurring = isRecurring,
                        createdAt = LocalDateTime.now(),
                        updatedAt = LocalDateTime.now(),
                        currency = currency,
                        billingCycle = billingCycle,
                        attachments = attachments
                )

        // Insert the transaction
        val transactionId = transactionRepository.insertTransaction(transaction)

        // On-chain anchoring: fire-and-forget via WorkManager, never a
        // blocking network call -- this app is offline-first, and
        // anchoring is a best-effort secondary feature (see
        // ChainAnchorWorker's doc comment). Requires network connectivity
        // (Constraints), and retries with exponential backoff on
        // transient failure rather than giving up on the first attempt.
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

        // Update account balances based on transaction type
        if (bankName != null && accountLast4 != null) {
            when (type) {
                TransactionType.INCOME -> {
                    val currentBalance = accountBalanceRepository.getLatestBalance(bankName, accountLast4)
                    val newBalance = (currentBalance?.balance ?: BigDecimal.ZERO) + amount
                    accountBalanceRepository.insertBalance(
                        AccountBalanceEntity(
                            bankName = bankName,
                            accountLast4 = accountLast4,
                            balance = newBalance,
                            timestamp = date,
                            transactionId = transactionId,
                            sourceType = "MANUAL",
                            iconResId = currentBalance?.iconResId ?: 0,
                            isCreditCard = currentBalance?.isCreditCard ?: false,
                            creditLimit = currentBalance?.creditLimit,
                            currency = currency
                        )
                    )
                }
                TransactionType.EXPENSE -> {
                    // Subtract amount from the selected account
                    val currentBalance = accountBalanceRepository.getLatestBalance(bankName, accountLast4)
                    val newBalance = (currentBalance?.balance ?: BigDecimal.ZERO) - amount
                    accountBalanceRepository.insertBalance(
                        AccountBalanceEntity(
                            bankName = bankName,
                            accountLast4 = accountLast4,
                            balance = newBalance,
                            timestamp = date,
                            transactionId = transactionId,
                            sourceType = "MANUAL",
                            iconResId = currentBalance?.iconResId ?: 0,
                            isCreditCard = currentBalance?.isCreditCard ?: false,
                            creditLimit = currentBalance?.creditLimit,
                            currency = currency
                        )
                    )
                }
                TransactionType.CREDIT -> {
                    // Credit transactions: amount is held for manual confirmation
                    // No balance update until user confirms payment
                    // Transaction is saved for future reference
                }
                TransactionType.TRANSFER -> {
                    // Transfer: subtract from source, add to target
                    if (targetAccountBankName != null && targetAccountLast4 != null) {
                        // Subtract from source account
                        val sourceBalance = accountBalanceRepository.getLatestBalance(bankName, accountLast4)
                        val newSourceBalance = (sourceBalance?.balance ?: BigDecimal.ZERO) - amount
                        accountBalanceRepository.insertBalance(
                            AccountBalanceEntity(
                                bankName = bankName,
                                accountLast4 = accountLast4,
                                balance = newSourceBalance,
                                timestamp = date,
                                transactionId = transactionId,
                                sourceType = "MANUAL",
                                iconResId = sourceBalance?.iconResId ?: 0,
                                isCreditCard = sourceBalance?.isCreditCard ?: false,
                                creditLimit = sourceBalance?.creditLimit,
                                currency = currency
                            )
                        )
                        
                        // Add to target account
                        val targetBalance = accountBalanceRepository.getLatestBalance(targetAccountBankName, targetAccountLast4)
                        val newTargetBalance = (targetBalance?.balance ?: BigDecimal.ZERO) + amount
                        accountBalanceRepository.insertBalance(
                            AccountBalanceEntity(
                                bankName = targetAccountBankName,
                                accountLast4 = targetAccountLast4,
                                balance = newTargetBalance,
                                timestamp = date,
                                transactionId = transactionId,
                                sourceType = "MANUAL",
                                iconResId = targetBalance?.iconResId ?: 0,
                                isCreditCard = targetBalance?.isCreditCard ?: false,
                                creditLimit = targetBalance?.creditLimit,
                                currency = currency
                            )
                        )
                    }
                }
                TransactionType.INVESTMENT -> {
                    // Subtract amount from the selected account
                    val currentBalance = accountBalanceRepository.getLatestBalance(bankName, accountLast4)
                    val newBalance = (currentBalance?.balance ?: BigDecimal.ZERO) - amount
                    accountBalanceRepository.insertBalance(
                        AccountBalanceEntity(
                            bankName = bankName,
                            accountLast4 = accountLast4,
                            balance = newBalance,
                            timestamp = date,
                            transactionId = transactionId,
                            sourceType = "MANUAL",
                            iconResId = currentBalance?.iconResId ?: 0,
                            isCreditCard = currentBalance?.isCreditCard ?: false,
                            creditLimit = currentBalance?.creditLimit,
                            currency = currency
                        )
                    )
                }
            }
        }

        // If marked as recurring, create a subscription
        if (createSubscription && isRecurring && transactionId != -1L) {
            val nextPaymentDate = SubscriptionUtils.calculateNextPaymentDate(date.toLocalDate(), billingCycle)

            val subscription =
                    SubscriptionEntity(
                            merchantName = merchant,
                            amount = amount,
                            nextPaymentDate = nextPaymentDate,
                            state = SubscriptionState.ACTIVE,
                            bankName = bankName ?: "Manual Entry",
                            category = category,
                            subcategory = subcategory,
                            createdAt = LocalDateTime.now(),
                            updatedAt = LocalDateTime.now(),
                            currency = currency,
                            billingCycle = billingCycle,
                            lastPaidDate = date.toLocalDate()
                    )

            subscriptionRepository.insertSubscription(subscription)
        }
    }


    private fun generateManualTransactionHash(
            amount: BigDecimal,
            merchant: String,
            date: LocalDateTime
    ): String {
        // Create a unique hash for manual transactions
        // Format: MANUAL_<amount>_<merchant>_<datetime>
        val data = "MANUAL_${amount}_${merchant}_${date}"

        return MessageDigest.getInstance("MD5").digest(data.toByteArray()).joinToString("") {
            "%02x".format(it)
        }
    }
}
