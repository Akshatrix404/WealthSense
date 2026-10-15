package com.wealthsense.app.data.manager

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import com.wealthsense.app.MainActivity
import com.wealthsense.app.R
import com.wealthsense.app.data.database.entity.TransactionEntity
import com.wealthsense.app.data.preferences.UserPreferencesRepository
import com.wealthsense.app.receiver.ReminderReceiver
import com.wealthsense.app.utils.CurrencyFormatter
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.first
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Handles scheduling of notification-related tasks using AlarmManager for precision.
 */
@Singleton
class NotificationScheduler @Inject constructor(
    @ApplicationContext private val context: Context,
    private val userPreferencesRepository: UserPreferencesRepository
) {
    private val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager

    companion object {
        private const val TAG = "NotificationScheduler"
        private const val ALARM_REQUEST_CODE = 200

        // Separate from SmsBroadcastReceiver.CHANNEL_ID ("transaction
        // notifications") deliberately: anomaly alerts are a distinct
        // category a user might want to control independently (e.g. keep
        // regular transaction notifications off but anomaly alerts on).
        private const val ANOMALY_CHANNEL_ID = "anomaly_alerts"
        private const val ANOMALY_CHANNEL_NAME = "Unusual Spending Alerts"
    }

    /**
     * Shows an immediate notification for a transaction flagged anomalous
     * by AnomalyDetectionService. Fire-and-forget: any failure here is
     * logged, not thrown -- a notification failing to show should never
     * be treated as a processing error (the transaction itself is
     * already saved by the time this is called).
     */
    /** Heads-up notification for every processed SMS transaction (demo + real). */
    fun showTransactionAlert(id: Int, title: String, text: String) {
        try {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(
                NotificationChannel("txn_alerts", "Transaction alerts", NotificationManager.IMPORTANCE_HIGH)
                    .apply { description = "Instant alerts for every debit and credit" }
            )
            val pi = PendingIntent.getActivity(
                context, id,
                Intent(context, MainActivity::class.java).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                },
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val n = NotificationCompat.Builder(context, "txn_alerts")
                .setSmallIcon(R.drawable.cashiro)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build()
            nm.notify(id, n)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to show transaction alert", e)
        }
    }

    fun showAnomalyAlert(transaction: TransactionEntity) {
        try {
            val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

            val channel = NotificationChannel(
                ANOMALY_CHANNEL_ID,
                ANOMALY_CHANNEL_NAME,
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Alerts for spending that looks unusual compared to your history"
            }
            notificationManager.createNotificationChannel(channel)

            val intent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pendingIntent = PendingIntent.getActivity(
                context,
                transaction.id.toInt(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )

            val formattedAmount = CurrencyFormatter.formatCurrency(transaction.amount)
            val merchant = transaction.merchantName

            val notification = NotificationCompat.Builder(context, ANOMALY_CHANNEL_ID)
                .setSmallIcon(R.drawable.cashiro)
                .setContentTitle("Unusual spending detected")
                .setContentText("You spent $formattedAmount on $merchant \u2014 that's more than usual. Want to see why?")
                .setStyle(NotificationCompat.BigTextStyle().bigText(
                    "You spent $formattedAmount on $merchant \u2014 that's more than usual for this category. Tap to review."
                ))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .build()

            notificationManager.notify(transaction.id.toInt(), notification)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to show anomaly alert for transaction ${transaction.id}", e)
        }
    }

    /**
     * Schedules or updates the daily reminder notification using AlarmManager.
     */
    suspend fun scheduleDailyReminder() {
        val scanEnabled = userPreferencesRepository.scanNewTransactionsEnabled.first()
        val upcomingEnabled = userPreferencesRepository.upcomingNotificationsEnabled.first()
        val alertTimeMinutes = userPreferencesRepository.scanNewTransactionsAlertTime.first()

        if (!scanEnabled && !upcomingEnabled) {
            Log.d(TAG, "Notifications disabled, cancelling alarm")
            cancelDailyReminder()
            return
        }

        val currentTime = LocalDateTime.now()
        val alertTime = LocalTime.of((alertTimeMinutes / 60).toInt(), (alertTimeMinutes % 60).toInt())
        var scheduledTime = LocalDateTime.of(currentTime.toLocalDate(), alertTime)

        // If the time has already passed today, schedule for tomorrow
        if (currentTime.isAfter(scheduledTime)) {
            scheduledTime = scheduledTime.plusDays(1)
        }

        // Add 10 seconds buffer if the scheduled time is basically "now" to avoid immediate trigger
        // if the calculation happened just as the minute changed.
        if (scheduledTime.isBefore(currentTime.plusSeconds(10))) {
            scheduledTime = scheduledTime.plusDays(1)
        }

        val triggerAtMillis = scheduledTime.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()

        Log.d(TAG, "Scheduling daily reminder at $scheduledTime ($triggerAtMillis ms)")

        val intent = Intent(context, ReminderReceiver::class.java)
        val pendingIntent = PendingIntent.getBroadcast(
            context,
            ALARM_REQUEST_CODE,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        // Use setExactAndAllowWhileIdle for precision even in battery saving modes
        try {
            alarmManager.setExactAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP,
                triggerAtMillis,
                pendingIntent
            )
        } catch (e: SecurityException) {
            Log.e(TAG, "SecurityException: SCHEDULE_EXACT_ALARM permission not granted, falling back to setAndAllowWhileIdle", e)
            alarmManager.setAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP,
                triggerAtMillis,
                pendingIntent
            )
        }
    }

    /**
     * Cancels the scheduled daily reminder.
     */
    fun cancelDailyReminder() {
        Log.d(TAG, "Cancelling daily reminder alarm")
        val intent = Intent(context, ReminderReceiver::class.java)
        val pendingIntent = PendingIntent.getBroadcast(
            context,
            ALARM_REQUEST_CODE,
            intent,
            PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE
        )
        if (pendingIntent != null) {
            alarmManager.cancel(pendingIntent)
        }
    }
}
