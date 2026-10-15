package com.wealthsense.app.di

import com.wealthsense.app.data.service.LlmServiceImpl
import com.wealthsense.app.domain.service.LlmService
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class LlmModule {
    
    @Binds
    @Singleton
    abstract fun bindLlmService(
        llmServiceImpl: LlmServiceImpl
    ): LlmService
}