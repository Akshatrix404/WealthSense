package com.wealthsense.app.data.auth

import javax.inject.Inject
import javax.inject.Singleton

sealed interface AuthResult {
    data object Success : AuthResult
    data class Error(val message: String) : AuthResult
}

/**
 * Simulated, local-only authentication -- no real bank/account linking,
 * no server, no network call. Credentials live in AuthDatabase (a Room
 * database entirely separate from the per-user financial database, see
 * AuthDatabase.kt's doc comment).
 */
@Singleton
class AuthRepository @Inject constructor(
    private val userAccountDao: UserAccountDao
) {
    suspend fun signup(username: String, password: String): AuthResult {
        val trimmed = username.trim()
        if (trimmed.isEmpty()) return AuthResult.Error("Username can't be empty")
        if (trimmed.length < 3) return AuthResult.Error("Username must be at least 3 characters")
        if (password.length < 6) return AuthResult.Error("Password must be at least 6 characters")

        if (userAccountDao.countByUsername(trimmed) > 0) {
            return AuthResult.Error("That username is already taken")
        }

        val (hash, salt) = PasswordHasher.hash(password)
        userAccountDao.insert(
            UserAccountEntity(
                username = trimmed,
                passwordHash = hash,
                passwordSalt = salt,
                createdAtEpochMillis = System.currentTimeMillis()
            )
        )
        return AuthResult.Success
    }

    suspend fun login(username: String, password: String): AuthResult {
        val trimmed = username.trim()
        val user = userAccountDao.getByUsername(trimmed)
            ?: return AuthResult.Error("No account found for that username")

        return if (PasswordHasher.verify(password, user.passwordSalt, user.passwordHash)) {
            AuthResult.Success
        } else {
            AuthResult.Error("Incorrect password")
        }
    }
}
