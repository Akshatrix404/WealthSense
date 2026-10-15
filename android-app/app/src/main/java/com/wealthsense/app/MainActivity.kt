package com.wealthsense.app

import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.appcompat.app.AppCompatActivity
import com.wealthsense.app.receiver.SmsBroadcastReceiver
import com.wealthsense.app.data.manager.NotificationScheduler
import androidx.lifecycle.lifecycleScope
import com.wealthsense.app.presentation.ui.features.accounts.AccountDetailViewModel
import com.wealthsense.app.presentation.ui.features.accounts.ManageAccountsViewModel
import com.wealthsense.app.presentation.ui.features.add.AddViewModel
import com.wealthsense.app.presentation.ui.features.analytics.AnalyticsViewModel
import com.wealthsense.app.presentation.ui.features.budgets.BudgetViewModel
import com.wealthsense.app.presentation.ui.features.categories.CategoriesViewModel
import com.wealthsense.app.presentation.ui.features.chat.ChatViewModel
import com.wealthsense.app.presentation.ui.features.home.HomeViewModel
import com.wealthsense.app.presentation.ui.features.onboarding.OnBoardingViewModel
import com.wealthsense.app.presentation.ui.features.profile.ProfileViewModel
import com.wealthsense.app.presentation.ui.features.settings.SettingsViewModel
import com.wealthsense.app.presentation.ui.features.settings.appearance.ThemeViewModel
import com.wealthsense.app.presentation.ui.features.settings.applock.AppLockViewModel
import com.wealthsense.app.presentation.ui.features.settings.notifications.NotificationViewModel
import com.wealthsense.app.presentation.ui.features.settings.rules.RulesViewModel
import com.wealthsense.app.presentation.ui.features.settings.unrecognized.UnrecognizedSmsViewModel
import com.wealthsense.app.presentation.ui.features.spotlight.SpotlightViewModel
import com.wealthsense.app.presentation.ui.features.subscriptions.SubscriptionsViewModel
import com.wealthsense.app.presentation.ui.features.transactions.TransactionDetailViewModel
import com.wealthsense.app.presentation.ui.features.transactions.TransactionsViewModel
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject
import kotlin.getValue

@AndroidEntryPoint
class MainActivity : AppCompatActivity() {
    companion object {
        const val ACTION_ADD_TRANSACTION = "com.wealthsense.app.action.ADD_TRANSACTION"
        const val ACTION_ADD_SUBSCRIPTION = "com.wealthsense.app.action.ADD_SUBSCRIPTION"
        const val ACTION_ADD_TRANSFER = "com.wealthsense.app.action.ADD_TRANSFER"
    }

    private val themeViewModel: ThemeViewModel by viewModels()
    private val appLockViewModel: AppLockViewModel by viewModels()
    
    @Inject
    lateinit var notificationScheduler: NotificationScheduler

    @Inject
    lateinit var sessionManager: com.wealthsense.app.data.auth.SessionManager

    // Transaction ID to edit when launched from notification
    var editTransactionId by mutableStateOf<Long?>(null)
        private set

    // Initial tab to show in Add Screen (0 for Transaction, 1 for Subscription)
    var addTransactionTab by mutableStateOf<Int?>(null)
        private set

    // Initial transaction type to pre-select
    var addTransactionType by mutableStateOf<String?>(null)
        private set

    override fun onCreate(savedInstanceState: Bundle?) {
        // Install splash screen before super.onCreate()
        val splashScreen = installSplashScreen()

        super.onCreate(savedInstanceState)
        
        // NOTE: previously gated on `!themeViewModel.themeUiState.value.isLoaded`,
        // which only ever became true once something actually SUBSCRIBED
        // to themeViewModel's state flow (CashiroApp did this
        // unconditionally before login/signup existed). For a logged-out
        // user, only AuthScreen() composes now, which never touches
        // themeViewModel -- so that subscription never happened, isLoaded
        // never flipped, and the splash stayed up forever even though the
        // real content was already rendering underneath it. Not worth
        // that risk for a cosmetic dark/light-theme flash -- dismiss
        // immediately instead.
        splashScreen.setKeepOnScreenCondition { false }
        enableEdgeToEdge()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            window.isNavigationBarContrastEnforced = false
//            window.isStatusBarContrastEnforced = false
        }

        // Handle intent if activity is launched from notification or shortcut/tile
        handleIntent(intent)

        // Schedule daily reminders
        lifecycleScope.launch {
            notificationScheduler.scheduleDailyReminder()
        }

        setContent {
            if (!sessionManager.isLoggedIn()) {
                // Simulated local login/signup -- see AuthScreen.kt and
                // DatabaseModule.provideCashiroDatabase()'s doc comment
                // for how a successful login here actually switches
                // which per-user database file the rest of the app uses.
                com.wealthsense.app.presentation.ui.features.auth.AuthScreen()
            } else {
                CashiroApp(
                    editTransactionId = editTransactionId,
                    onEditComplete = { editTransactionId = null },
                    addTransactionTab = addTransactionTab,
                    addTransactionType = addTransactionType,
                    onAddComplete = { 
                        addTransactionTab = null
                        addTransactionType = null
                    },
                    appLockViewModel = appLockViewModel,
                    themeViewModel = themeViewModel,
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Handle intent when activity is already running
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        when (intent?.action) {
            SmsBroadcastReceiver.ACTION_EDIT_TRANSACTION -> {
                val transactionId = intent.getLongExtra(SmsBroadcastReceiver.EXTRA_TRANSACTION_ID, -1)
                if (transactionId != -1L) {
                    editTransactionId = transactionId
                }
            }
            ACTION_ADD_TRANSACTION -> {
                addTransactionTab = 0
            }
            ACTION_ADD_SUBSCRIPTION -> {
                addTransactionTab = 1
            }
            ACTION_ADD_TRANSFER -> {
                addTransactionTab = 0
                addTransactionType = "TRANSFER"
            }
        }
    }
}
