package com.wealthsense.app.data.auth

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * A registered login. Deliberately separate from CashiroDatabase (the
 * per-user financial data database, see AuthModule.kt) -- this table is
 * the single, always-shared registry of "which usernames exist and what
 * their password hash is." It never contains transactions, balances, or
 * any financial data; that all lives in a separate, per-user database
 * file selected at app-launch time based on SessionManager's active
 * username (see DatabaseModule.kt).
 */
@Entity(tableName = "user_accounts")
data class UserAccountEntity(
    @PrimaryKey val username: String,
    val passwordHash: String,
    val passwordSalt: String,
    val createdAtEpochMillis: Long
)
