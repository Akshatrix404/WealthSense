package com.wealthsense.app.domain.service

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.wealthsense.app.data.database.CashiroDatabase
import com.wealthsense.app.data.database.entity.TransactionEntity
import com.wealthsense.app.data.database.entity.TransactionType
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.math.BigDecimal
import java.time.LocalDateTime

/**
 * Feeds 5 known-normal and 2 known-anomalous transactions through
 * AnomalyDetectionService and asserts correct classification, as
 * specified.
 *
 * WHY THIS IS AN INSTRUMENTED TEST, NOT A LOCAL JVM UNIT TEST:
 * AnomalyDetectionService loads a real ONNX model via onnxruntime-android,
 * which depends on native (.so) libraries that only load in a real
 * Android runtime -- a plain local JVM unit test (app/src/test) has no
 * mechanism to load them. This has to run on a device or emulator
 * (./gradlew connectedAndroidTest).
 *
 * TEST DESIGN NOTE: normal amounts are chosen close to the built-up
 * category history's mean (~Rs 200), and anomalous amounts are chosen
 * an order of magnitude beyond the 5-10x range the model was actually
 * trained to detect (Rs 5,000-8,000 vs. a ~Rs 200 mean), specifically so
 * the test has a wide, unambiguous margin rather than sitting near the
 * model's actual decision boundary -- this is a real trained model with
 * measured 68% recall (see ml-training/README.md), not a hand-written
 * rule, so borderline test amounts could legitimately go either way and
 * would make this test flaky rather than a meaningful correctness check.
 *
 * NOT ACTUALLY RUN during development: this sandbox has no Android
 * device/emulator, so this test is written and carefully reasoned
 * through but its actual pass/fail has not been observed. Run it
 * yourself with `./gradlew connectedAndroidTest` once you have an
 * emulator or device attached.
 */
@RunWith(AndroidJUnit4::class)
class AnomalyDetectionServiceTest {

    private lateinit var db: CashiroDatabase
    private lateinit var service: AnomalyDetectionService

    private val testUserCategory = "Food & Drinks"
    private val historyBaseDate: LocalDateTime = LocalDateTime.of(2026, 1, 1, 9, 0)

    @Before
    fun setUp() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        db = Room.inMemoryDatabaseBuilder(context, CashiroDatabase::class.java)
            .allowMainThreadQueries()
            .build()

        service = AnomalyDetectionService(context, db.transactionDao())

        // Build up 10 days of "Food & Drinks" history around Rs 200 so
        // amount_zscore has a real, non-trivial baseline to compare
        // against (fewer than 2 prior same-category transactions makes
        // the service default to a neutral 0.0 z-score, which would make
        // this test meaningless).
        val historyAmounts = listOf(180, 195, 210, 190, 205, 200, 185, 215, 198, 202)
        historyAmounts.forEachIndexed { i, amount ->
            db.transactionDao().insertTransaction(
                makeTransaction(amount = amount, daysOffset = i)
            )
        }
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun scoresFiveNormalTransactionsAsNotAnomalous() = runBlocking {
        // Amounts close to the Rs ~198 historical mean -- ordinary spend.
        val normalAmounts = listOf(190, 205, 200, 195, 210)

        for (amount in normalAmounts) {
            val txn = makeTransaction(amount = amount, daysOffset = 20)
            val id = db.transactionDao().insertTransaction(txn)
            val saved = txn.copy(id = id)

            val result = service.score(saved)
            assertTrue("Expected result for Rs $amount, got null", result != null)
            assertFalse(
                "Expected Rs $amount (near the Rs ~198 mean) to be classified NORMAL, " +
                    "but it was flagged anomalous (rawScore=${result?.rawScore})",
                result!!.isAnomalous
            )
        }
    }

    @Test
    fun scoresTwoAnomalousTransactionsAsAnomalous() = runBlocking {
        // ~25-40x the Rs ~198 historical mean -- an unambiguous spike,
        // well beyond the model's actual 5-10x training range, chosen
        // deliberately for margin (see class doc).
        val anomalousAmounts = listOf(5000, 8000)

        for (amount in anomalousAmounts) {
            val txn = makeTransaction(amount = amount, daysOffset = 21)
            val id = db.transactionDao().insertTransaction(txn)
            val saved = txn.copy(id = id)

            val result = service.score(saved)
            assertTrue("Expected result for Rs $amount, got null", result != null)
            assertTrue(
                "Expected Rs $amount (a ~25-40x spike over the Rs ~198 mean) to be " +
                    "flagged ANOMALOUS, but it was classified normal (rawScore=${result?.rawScore})",
                result!!.isAnomalous
            )
        }
    }

    private fun makeTransaction(amount: Int, daysOffset: Int): TransactionEntity {
        return TransactionEntity(
            amount = BigDecimal(amount),
            merchantName = "Test Merchant",
            category = testUserCategory,
            transactionType = TransactionType.EXPENSE,
            dateTime = historyBaseDate.plusDays(daysOffset.toLong()),
            transactionHash = "test-hash-$amount-$daysOffset",
            smsBody = null,
            smsSender = null
        )
    }
}
