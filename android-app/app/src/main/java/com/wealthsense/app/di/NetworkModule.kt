package com.wealthsense.app.di

import com.wealthsense.app.data.network.ChainAnchorApi
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    /**
     * Base URL for the FastAPI chain-anchor bridge (../../../../../../backend/).
     *
     * IMPORTANT, a classic real gotcha: from an Android EMULATOR, "localhost"
     * refers to the emulator's own loopback, NOT your host machine -- to
     * reach a backend running on your host machine during local
     * development, use 10.0.2.2 instead (Android emulator's documented
     * alias for the host loopback). A physical device needs your host
     * machine's real LAN IP instead. For anything beyond local dev,
     * point this at your actual deployed backend URL.
     *
     * TODO: replace with your real backend URL, or wire this to
     * BuildConfig / a remote config value per build variant before
     * shipping anywhere beyond local testing.
     */
    private const val BASE_URL = "http://10.0.2.2:8000/"

    @Provides
    @Singleton
    fun provideOkHttpClient(): OkHttpClient {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }
        return OkHttpClient.Builder()
            .addInterceptor(logging)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS) // chain calls can be slow (block confirmation)
            .writeTimeout(15, TimeUnit.SECONDS)
            .build()
    }

    @Provides
    @Singleton
    fun provideRetrofit(okHttpClient: OkHttpClient): Retrofit {
        return Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    @Provides
    @Singleton
    fun provideChainAnchorApi(retrofit: Retrofit): ChainAnchorApi {
        return retrofit.create(ChainAnchorApi::class.java)
    }
}
