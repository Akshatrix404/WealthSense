package com.wealthsense.app.data.auth

import androidx.room.Database
import androidx.room.RoomDatabase

/**
 * Separate, single, always-shared Room database (one file, regardless of
 * which user is currently logged in -- unlike CashiroDatabase, which is
 * opened per-user). Holds only login credentials, nothing financial.
 */
@Database(entities = [UserAccountEntity::class], version = 1, exportSchema = false)
abstract class AuthDatabase : RoomDatabase() {
    abstract fun userAccountDao(): UserAccountDao

    companion object {
        const val DATABASE_NAME = "wealthsense_auth.db"
    }
}
