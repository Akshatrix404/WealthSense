package com.wealthsense.app.domain.usecase

import android.util.Log
import com.wealthsense.app.data.database.entity.SubscriptionEntity
import com.wealthsense.app.data.repository.SubscriptionRepository
import javax.inject.Inject

class UpdateSubscriptionUseCase
@Inject
constructor(private val subscriptionRepository: SubscriptionRepository) {
    suspend fun execute(subscription: SubscriptionEntity) {
        Log.d("UpdateSubscriptionUseCase", "Updating subscription entity: ${subscription.id}")
        subscriptionRepository.updateSubscription(subscription)
        Log.d("UpdateSubscriptionUseCase", "Subscription updated successfully.")
    }
}
