package com.wealthsense.app.data.network

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.nio.ByteBuffer
import java.security.KeyStore
import javax.crypto.KeyGenerator
import javax.crypto.Mac
import javax.crypto.SecretKey

/**
 * Computes the on-chain commitment for a transaction:
 *
 *     c = HMAC-SHA256(K, "WS1" || len(f1)||f1 || ... || len(f5)||f5)
 *
 * K is a 256-bit HMAC key generated inside the Android Keystore. It is not
 * extractable, so it cannot be copied off the device, and the commitment cannot
 * be recomputed (and therefore cannot be brute-forced from the small space of
 * plausible amounts and categories) by anyone who only sees the public chain.
 * The byte layout is identical to backend/commitment.py, which holds the
 * reference implementation and test vectors.
 *
 * Trade-off: because K is non-extractable, commitments can only be re-verified
 * on the same device (or after the user has exported a key through a separate,
 * deliberate backup flow that is not part of this prototype).
 */
object CommitmentSigner {
    private const val KEYSTORE = "AndroidKeyStore"
    private const val ALIAS = "wealthsense_commitment_key"
    private val DOMAIN = "WS1".toByteArray(Charsets.UTF_8)

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_HMAC_SHA256, KEYSTORE)
        kg.init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN).build())
        return kg.generateKey()
    }

    fun canonicalMessage(amountPaise: Long, category: String, epochSec: Long, merchant: String, txType: String): ByteArray {
        val fields = listOf(amountPaise.toString(), category, epochSec.toString(), merchant, txType)
        val parts = fields.map { it.toByteArray(Charsets.UTF_8) }
        val buf = ByteBuffer.allocate(DOMAIN.size + parts.sumOf { 4 + it.size })
        buf.put(DOMAIN)
        for (p in parts) { buf.putInt(p.size); buf.put(p) }
        return buf.array()
    }

    /** Returns the commitment as 0x-prefixed lowercase hex (66 characters). */
    fun commit(amountPaise: Long, category: String, epochSec: Long, merchant: String, txType: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(key())
        val digest = mac.doFinal(canonicalMessage(amountPaise, category, epochSec, merchant, txType))
        return "0x" + digest.joinToString("") { "%02x".format(it) }
    }
}
