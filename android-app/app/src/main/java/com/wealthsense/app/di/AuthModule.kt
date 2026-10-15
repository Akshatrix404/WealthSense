package com.wealthsense.app.di

import android.content.Context
import androidx.room.Room
import com.wealthsense.app.data.auth.AuthDatabase
import com.wealthsense.app.data.auth.UserAccountDao
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AuthModule {

    @Provides
    @Singleton
    fun provideAuthDatabase(@ApplicationContext context: Context): AuthDatabase {
        return Room.databaseBuilder(context, AuthDatabase::class.java, AuthDatabase.DATABASE_NAME)
            .build()
    }

    @Provides
    @Singleton
    fun provideUserAccountDao(authDatabase: AuthDatabase): UserAccountDao {
        return authDatabase.userAccountDao()
    }
}
