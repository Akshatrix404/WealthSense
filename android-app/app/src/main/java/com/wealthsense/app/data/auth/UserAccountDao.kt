package com.wealthsense.app.data.auth

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query

@Dao
interface UserAccountDao {
    @Insert
    suspend fun insert(user: UserAccountEntity)

    @Query("SELECT * FROM user_accounts WHERE username = :username LIMIT 1")
    suspend fun getByUsername(username: String): UserAccountEntity?

    @Query("SELECT COUNT(*) FROM user_accounts WHERE username = :username")
    suspend fun countByUsername(username: String): Int
}
