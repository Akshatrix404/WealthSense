package com.wealthsense.app.data.manager

import android.util.Log
import com.wealthsense.app.data.database.entity.AccountBalanceEntity
import com.wealthsense.app.data.database.entity.TransactionEntity
import com.wealthsense.app.data.database.entity.TransactionType
import com.wealthsense.app.data.repository.AccountBalanceRepository
import com.wealthsense.app.data.repository.TransactionRepository
import com.wealthsense.app.domain.service.ForecastOutcome
import com.wealthsense.app.domain.service.ForecastService
import java.math.BigDecimal
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.Random
import javax.inject.Inject
import javax.inject.Singleton

enum class DemoSmsKind { RANDOM, SMALL_DAILY, PURCHASE, CREDIT }

data class DemoSmsOutcome(
    val success: Boolean,
    val title: String,
    val message: String
)

/**
 * DEMO / TESTING ONLY. Two jobs:
 *  1. [ensureSeeded]: gives the logged-in user 45 days of realistic history
 *     (small daily payments, bills, purchases, salary + named credits) on a
 *     demo HDFC Bank account, with a correct running balanceAfter on every
 *     row, so the 30-day forecaster has real input.
 *  2. [triggerSms]: builds a realistic HDFC account SMS (A/c format, so the
 *     parser treats it as an ACCOUNT transaction, not a card) for that same
 *     account and pushes it through the REAL SmsTransactionProcessor path.
 *     The SMS's "Avl bal" is computed from the account's current balance, so
 *     the balance genuinely moves, and the forecast is re-run before/after.
 */
@Singleton
class DemoDataService @Inject constructor(
    private val transactionRepository: TransactionRepository,
    private val accountBalanceRepository: AccountBalanceRepository,
    private val smsTransactionProcessor: SmsTransactionProcessor,
    private val notificationScheduler: NotificationScheduler,
    private val forecastService: ForecastService
) {
    companion object {
        private const val TAG = "DemoDataService"
        const val BANK = "HDFC Bank"
        const val LAST4 = "4821"
        private const val SENDER = "HDFCBK"
        private const val HASH_PREFIX = "demo-"
        private const val HISTORY_DAYS = 45

        private val SMALL = listOf(
            "Chai Point" to 20, "Tea Stall" to 20, "Amul Milk" to 30, "Newspaper Vendor" to 30,
            "Rapido" to 45, "Local Kirana" to 60, "Metro Card Recharge" to 50, "Parking Fee" to 40,
            "Samosa Corner" to 25, "Auto Rickshaw" to 35
        )
        private val PURCHASES = listOf(
            "Flipkart" to 1500, "Swiggy" to 420, "Zomato" to 350, "Amazon" to 899,
            "BigBasket" to 1250, "Uber" to 260, "Myntra" to 1799, "Dominos" to 549,
            "Starbucks" to 380, "Apollo Pharmacy" to 640, "Airtel Recharge" to 399, "Croma" to 2499
        )
        private val CREDITS = listOf(
            "Rahul Sharma" to 2000, "Priya Mehta" to 750, "Ankit Verma" to 1200,
            "Sneha Kapoor" to 500, "Infosys Technologies" to 3500, "Amazon Pay Refund" to 499,
            "Freelance Studio Pvt" to 4000
        )
        private val rnd = Random()
    }

    private val dateFmt = DateTimeFormatter.ofPattern("dd-MM-yy", Locale.ENGLISH)

    private fun fmt(v: Int) = String.format(Locale.US, "%,d", v)
    private fun ref() = (100000000000L + (rnd.nextDouble() * 899999999999L).toLong()).toString()

    private fun debitSms(amount: Int, merchant: String, date: LocalDate, bal: Int) =
        "Rs.$amount.00 debited from HDFC Bank A/c $LAST4 on ${date.format(dateFmt)}. " +
            "Info: UPI/$merchant/${ref()}. Avl bal: INR ${fmt(bal)}.00"

    private fun creditSms(amount: Int, sender: String, date: LocalDate, bal: Int) =
        "Rs.$amount.00 credited to HDFC Bank A/c $LAST4 on ${date.format(dateFmt)} " +
            "from VPA ${sender.lowercase().filter { it.isLetter() }.take(14)}@okaxis ($sender) " +
            "UPI Ref ${ref()}. Avl bal: INR ${fmt(bal)}.00"

    private fun salarySms(amount: Int, date: LocalDate, bal: Int) =
        "Rs.$amount.00 deposited in HDFC Bank A/c XX$LAST4 on ${date.format(dateFmt)} " +
            "for 00123-NEFT-CR ${date.month.name.take(3)} SALARY-INFOSYS LTD. Avl bal: INR ${fmt(bal)}.00"

    private fun categoryFor(merchant: String) = when (merchant) {
        "Swiggy", "Zomato", "Dominos", "Starbucks", "Chai Point", "Tea Stall", "Samosa Corner" -> "Food & Dining"
        "BigBasket", "Amul Milk", "Local Kirana" -> "Groceries"
        "Uber", "Rapido", "Metro Card Recharge", "Auto Rickshaw", "Parking Fee" -> "Transportation"
        "Flipkart", "Amazon", "Myntra", "Croma" -> "Shopping"
        "Airtel Recharge" -> "Mobile"
        "Apollo Pharmacy" -> "Healthcare"
        "Netflix", "Electricity Bill", "Broadband" -> "Bills & Utilities"
        else -> "Others"
    }

    // ---------------------------------------------------------------- seeding

    suspend fun isSeeded(): Boolean =
        transactionRepository.getAllTransactionsList().any { it.transactionHash.startsWith(HASH_PREFIX) }

    /** Seeds once per user DB. Returns true if it inserted data now. */
    suspend fun ensureSeeded(): Boolean {
        if (isSeeded()) return false

        val r = Random(42)
        val today = LocalDate.now()
        val txns = mutableListOf<TransactionEntity>()
        val balances = mutableListOf<AccountBalanceEntity>()
        var bal = 6000
        var idx = 0

        fun add(date: LocalDate, minuteOfDay: Int, merchant: String, amount: Int, income: Boolean, sms: (Int) -> String, category: String) {
            if (!income && amount > bal) return
            bal += if (income) amount else -amount
            val at = LocalDateTime.of(date, LocalTime.of((minuteOfDay / 60) % 24, minuteOfDay % 60, idx % 60))
            val body = sms(bal)
            txns += TransactionEntity(
                amount = BigDecimal(amount),
                merchantName = merchant,
                category = category,
                transactionType = if (income) TransactionType.INCOME else TransactionType.EXPENSE,
                dateTime = at,
                smsBody = body,
                bankName = BANK,
                smsSender = SENDER,
                accountNumber = LAST4,
                balanceAfter = BigDecimal(bal),
                transactionHash = "$HASH_PREFIX${idx++}-${at}"
            )
            balances += AccountBalanceEntity(
                bankName = BANK, accountLast4 = LAST4, balance = BigDecimal(bal),
                timestamp = at, sourceType = "TRANSACTION", smsSource = body.take(200)
            )
        }

        for (offset in HISTORY_DAYS downTo 1) {
            val d = today.minusDays(offset.toLong())
            // Salary every ~30 days, named company.
            if (offset == HISTORY_DAYS - 2 || offset == HISTORY_DAYS - 32) {
                add(d, 9 * 60, "Infosys Ltd", 30000, true, { b -> salarySms(30000, d, b) }, "Salary")
            }
            // 1-3 small daily payments (Rs 20-60).
            repeat(1 + r.nextInt(3)) { k ->
                val (m, a) = SMALL[r.nextInt(SMALL.size)]
                add(d, 8 * 60 + k * 95 + r.nextInt(40), m, a, false, { b -> debitSms(a, m, d, b) }, categoryFor(m))
            }
            // Food/ride/shopping every other day.
            if (offset % 2 == 0) {
                val (m, base) = PURCHASES[r.nextInt(PURCHASES.size - 2)]
                val a = (base * (0.6 + r.nextDouble() * 0.8)).toInt().coerceAtLeast(100)
                add(d, 19 * 60 + r.nextInt(120), m, a, false, { b -> debitSms(a, m, d, b) }, categoryFor(m))
            }
            // Credits from named people/companies roughly every 6 days.
            if (offset % 6 == 3) {
                val (who, base) = CREDITS[r.nextInt(CREDITS.size)]
                add(d, 14 * 60 + r.nextInt(60), who, base, true, { b -> creditSms(base, who, d, b) },
                    if (who.contains("Refund")) "Others" else "Income")
            }
            // Monthly bills.
            if (offset == 20 || offset == 50 - 5) {
                add(d, 11 * 60, "Electricity Bill", 1240, false, { b -> debitSms(1240, "Electricity Bill", d, b) }, "Bills & Utilities")
            }
            if (offset == 12) {
                add(d, 10 * 60, "Netflix", 199, false, { b -> debitSms(199, "Netflix", d, b) }, "Bills & Utilities")
            }
        }

        transactionRepository.insertTransactions(txns)
        balances.forEach { accountBalanceRepository.insertBalance(it) }
        Log.d(TAG, "Seeded ${txns.size} demo transactions, final balance Rs $bal")
        return true
    }

    // --------------------------------------------------------------- triggers

    private suspend fun forecastValue(): Double? =
        (forecastService.getForecast() as? ForecastOutcome.Success)?.result?.predictedBalance

    suspend fun triggerSms(kind: DemoSmsKind): DemoSmsOutcome {
        ensureSeeded()
        val before = accountBalanceRepository.getLatestBalance(BANK, LAST4)?.balance?.toInt() ?: 0
        val fcBefore = forecastValue()

        val pick = if (kind == DemoSmsKind.RANDOM) {
            when (rnd.nextInt(10)) {
                in 0..3 -> DemoSmsKind.SMALL_DAILY
                in 4..7 -> DemoSmsKind.PURCHASE
                else -> DemoSmsKind.CREDIT
            }
        } else kind

        val today = LocalDate.now()
        var credit = pick == DemoSmsKind.CREDIT
        var choice = when (pick) {
            DemoSmsKind.SMALL_DAILY -> SMALL.random()
            DemoSmsKind.PURCHASE -> PURCHASES.random()
            else -> CREDITS.random()
        }
        if (!credit && choice.second > before) { // can't overspend the account: send a credit instead
            credit = true
            choice = CREDITS.random()
        }
        val merchant = choice.first
        val amount = choice.second

        val after = if (credit) before + amount else before - amount
        val body = if (credit) creditSms(amount, merchant, today, after) else debitSms(amount, merchant, today, after)

        val result = smsTransactionProcessor.processAndSaveTransaction(
            sender = SENDER, body = body, timestamp = System.currentTimeMillis()
        )
        if (!result.success) {
            return DemoSmsOutcome(false, "SMS not processed", result.reason ?: "Unknown reason")
        }

        val fcAfter = forecastValue()
        val verb = if (credit) "credited" else "debited"
        val title = "₹${fmt(amount)} $verb — $merchant"
        val sb = StringBuilder()
        sb.append(if (credit) "Received from $merchant." else "Paid to $merchant.")
        sb.append("\nBalance: ₹${fmt(before)} → ₹${fmt(after)}")
        if (fcAfter != null) {
            sb.append("\n7-day forecast: ")
            if (fcBefore != null) sb.append("₹${fmt(fcBefore.toInt())} → ")
            sb.append("₹${fmt(fcAfter.toInt())}")
        }
        notificationScheduler.showTransactionAlert(
            id = (result.transactionId ?: System.currentTimeMillis()).toInt(),
            title = title,
            text = "New balance ₹${fmt(after)}" +
                (fcAfter?.let { " • 7-day forecast ₹${fmt(it.toInt())}" } ?: "")
        )
        return DemoSmsOutcome(true, title, sb.toString())
    }
}
