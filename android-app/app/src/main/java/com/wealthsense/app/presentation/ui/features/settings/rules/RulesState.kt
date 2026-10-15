package com.wealthsense.app.presentation.ui.features.settings.rules

import com.wealthsense.app.domain.usecase.BatchApplyResult

data class RulesUiState(
    val isLoading: Boolean = false,
    val batchApplyProgress: Pair<Int, Int>? = null,
    val batchApplyResult: BatchApplyResult? = null
)
