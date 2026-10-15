package com.wealthsense.app.data.auth

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * SHA-256 + per-user random salt password hashing. Not a substitute for
 * a real production KDF (bcrypt/scrypt/Argon2 would be the real choice
 * for an actual product, since SHA-256 is fast-to-brute-force by design
 * -- fine for a fast general-purpose hash, not ideal for passwords at
 * scale). Appropriate here because this is an explicitly simulated,
 * local-only, single-device auth system with no real account linking --
 * but it's still a real salted hash, not plaintext storage, because
 * "simulated" shouldn't mean "insecure by construction" as a matter of
 * habit.
 */
object PasswordHasher {
    private const val SALT_BYTES = 16

    data class HashResult(val hash: String, val salt: String)

    fun hash(password: String): HashResult {
        val salt = ByteArray(SALT_BYTES).also { SecureRandom().nextBytes(it) }
        val saltB64 = Base64.getEncoder().encodeToString(salt)
        return HashResult(hash = hashWithSalt(password, saltB64), salt = saltB64)
    }

    fun verify(password: String, salt: String, expectedHash: String): Boolean {
        return hashWithSalt(password, salt) == expectedHash
    }

    private fun hashWithSalt(password: String, saltB64: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update(Base64.getDecoder().decode(saltB64))
        val hashed = digest.digest(password.toByteArray(Charsets.UTF_8))
        return Base64.getEncoder().encodeToString(hashed)
    }
}
