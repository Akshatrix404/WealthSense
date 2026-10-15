package com.wealthsense.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tracks which user is currently logged in. Deliberately backed by plain
 * SharedPreferences, NOT DataStore -- DataStore is Flow/coroutine-based
 * (asynchronous), and the active username needs to be readable
 * SYNCHRONOUSLY at Hilt's Room-database-provider construction time (see
 * DatabaseModule.kt, which picks the per-user database FILENAME based on
 * this value before any coroutine could run).
 *
 * Username persistence uses commit() (blocking), not apply() (async),
 * specifically at login/signup time -- the app process gets restarted
 * immediately after a successful login (see AuthViewModel) so the new
 * database file gets picked up on the fresh process's Hilt graph, and
 * that restart must never race an unflushed apply() write.
 */
@Singleton
class SessionManager @Inject constructor(
    @ApplicationContext context: Context
) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getCurrentUsername(): String? = prefs.getString(KEY_USERNAME, null)

    fun isLoggedIn(): Boolean = getCurrentUsername() != null

    /** Blocking write (commit, not apply) -- see class doc for why. */
    fun setCurrentUsername(username: String) {
        prefs.edit().putString(KEY_USERNAME, username).commit()
    }

    /** Blocking write, same reasoning as setCurrentUsername. */
    fun logout() {
        prefs.edit().remove(KEY_USERNAME).commit()
    }

    companion object {
        // Public so CashiroDatabase's static getInstance() (used by
        // BroadcastReceivers, which have no Hilt injection) can read the
        // exact same session state directly via SharedPreferences,
        // without needing a circular Hilt dependency on this class.
        const val PREFS_NAME = "wealthsense_session"
        const val KEY_USERNAME = "current_username"
    }
}
