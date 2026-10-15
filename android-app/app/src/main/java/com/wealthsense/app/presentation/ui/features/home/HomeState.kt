package com.wealthsense.app.presentation.ui.features.home

import android.net.Uri
import androidx.compose.ui.graphics.Color
import com.wealthsense.app.data.database.entity.AccountBalanceEntity
import com.wealthsense.app.data.database.entity.SubscriptionEntity
import com.wealthsense.app.data.database.entity.TransactionEntity
import com.wealthsense.app.data.repository.BudgetWithSpending
import com.wealthsense.app.presentation.ui.components.BalancePoint
import java.math.BigDecimal

/**
 * UI-level state for the Home screen's forecast card. Distinct from
 * domain.service.ForecastOutcome because "Loading" is purely a
 * presentation-layer concern -- ForecastService itself is a one-shot
 * suspend call with no notion of an in-progress state.
 */
sealed interface ForecastCardState {
    data object Loading : ForecastCardState
    data class NotEnoughData(val daysAvailable: Int, val daysRequired: Int) : ForecastCardState
    data class Success(
        val predictedBalance: Double,
        val confidenceRangeLow: Double,
        val confidenceRangeHigh: Double
    ) : ForecastCardState
    data class Error(val message: String) : ForecastCardState
}

data class HomeUiState(
    val currentMonthTotal: BigDecimal = BigDecimal.ZERO,
    val currentYearTotal: BigDecimal = BigDecimal.ZERO,
    val currentMonthIncome: BigDecimal = BigDecimal.ZERO,
    val currentMonthExpenses: BigDecimal = BigDecimal.ZERO,
    val currentYearExpenses: BigDecimal = BigDecimal.ZERO,
    val currentMonthCreditCard: BigDecimal = BigDecimal.ZERO,
    val currentMonthTransfer: BigDecimal = BigDecimal.ZERO,
    val currentMonthInvestment: BigDecimal = BigDecimal.ZERO,
    val lastMonthTotal: BigDecimal = BigDecimal.ZERO,
    val lastMonthIncome: BigDecimal = BigDecimal.ZERO,
    val lastMonthExpenses: BigDecimal = BigDecimal.ZERO,
    val monthlyChange: BigDecimal = BigDecimal.ZERO,
    val monthlyChangePercent: Int = 0,
    val recentTransactions: List<TransactionEntity> = emptyList(),
    val upcomingSubscriptions: List<SubscriptionEntity> = emptyList(),
    val upcomingSubscriptionsTotal: BigDecimal = BigDecimal.ZERO,
    val upcomingSubscriptionsCurrency: String = "INR",
    val accountBalances: List<AccountBalanceEntity> = emptyList(),
    val creditCards: List<AccountBalanceEntity> = emptyList(),
    val totalBalance: BigDecimal = BigDecimal.ZERO,
    val totalAvailableCredit: BigDecimal = BigDecimal.ZERO,
    val selectedCurrency: String = "INR",
    val availableCurrencies: List<String> = emptyList(),
    val isLoading: Boolean = true,
    val isScanning: Boolean = false,
    val showBreakdownDialog: Boolean = false,
    val userName: String = "User",
    val profileImageUri: Uri? = null,
    val profileBackgroundColor: Color = Color.Transparent,
    val unreadUpdatesCount: Int = 0,
    val bannerImageUri: Uri? = null,
    val showBannerImage: Boolean = false,
    val activeBudgets: List<BudgetWithSpending> = emptyList(),
    val balanceHistory: List<BalancePoint> = emptyList(),
    val transactionHeatmap: Map<java.time.LocalDate, Int> = emptyMap(),
    val convertedAmounts: Map<Long, BigDecimal> = emptyMap(),
    val baseCurrency: String = "INR",
    val forecastState: ForecastCardState = ForecastCardState.Loading,
    /** One-shot feedback after tapping "Send Test SMS" -- set with a
     *  result message, then cleared by the UI once shown (Snackbar). */
    val testSmsMessage: String? = null,
)
