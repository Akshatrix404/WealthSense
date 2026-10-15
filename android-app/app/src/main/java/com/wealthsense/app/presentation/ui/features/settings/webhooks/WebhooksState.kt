package com.wealthsense.app.presentation.ui.features.settings.webhooks

import com.wealthsense.app.data.database.entity.WebhookLogEntity
import com.wealthsense.app.data.database.entity.WebhookProfileEntity
import com.wealthsense.app.data.webhook.WebhookSettings

data class WebhooksUiState(
    val profiles: List<WebhookProfileEntity> = emptyList(),
    val logs: List<WebhookLogEntity> = emptyList(),
    val settings: WebhookSettings = WebhookSettings(),
    val settingsLoaded: Boolean = false,
    val isSyncing: Boolean = false,
    val message: String? = null
)
