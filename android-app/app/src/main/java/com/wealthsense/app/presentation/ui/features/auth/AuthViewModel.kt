package com.wealthsense.app.presentation.ui.features.auth

import android.app.Activity
import android.content.Context
import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.wealthsense.app.MainActivity
import com.wealthsense.app.data.auth.AuthRepository
import com.wealthsense.app.data.auth.AuthResult
import com.wealthsense.app.data.auth.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class AuthUiState(
    val username: String = "",
    val password: String = "",
    val confirmPassword: String = "", // signup only
    val isLoading: Boolean = false,
    val errorMessage: String? = null
)

@HiltViewModel
class AuthViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val sessionManager: SessionManager,
    @ApplicationContext private val context: Context
) : ViewModel() {

    private val _uiState = MutableStateFlow(AuthUiState())
    val uiState: StateFlow<AuthUiState> = _uiState.asStateFlow()

    fun onUsernameChange(value: String) {
        _uiState.value = _uiState.value.copy(username = value, errorMessage = null)
    }

    fun onPasswordChange(value: String) {
        _uiState.value = _uiState.value.copy(password = value, errorMessage = null)
    }

    fun onConfirmPasswordChange(value: String) {
        _uiState.value = _uiState.value.copy(confirmPassword = value, errorMessage = null)
    }

    fun login() {
        val state = _uiState.value
        _uiState.value = state.copy(isLoading = true, errorMessage = null)

        viewModelScope.launch {
            when (val result = authRepository.login(state.username, state.password)) {
                is AuthResult.Success -> onAuthSuccess(state.username.trim())
                is AuthResult.Error -> _uiState.value =
                    _uiState.value.copy(isLoading = false, errorMessage = result.message)
            }
        }
    }

    fun signup() {
        val state = _uiState.value
        if (state.password != state.confirmPassword) {
            _uiState.value = state.copy(errorMessage = "Passwords don't match")
            return
        }
        _uiState.value = state.copy(isLoading = true, errorMessage = null)

        viewModelScope.launch {
            when (val result = authRepository.signup(state.username, state.password)) {
                is AuthResult.Success -> onAuthSuccess(state.username.trim())
                is AuthResult.Error -> _uiState.value =
                    _uiState.value.copy(isLoading = false, errorMessage = result.message)
            }
        }
    }

    /**
     * On successful login/signup: persist the session (BLOCKING write --
     * see SessionManager's doc comment on why commit() not apply()), then
     * restart the app process so Hilt rebuilds its whole graph fresh --
     * specifically so CashiroDatabase's @Singleton provider re-reads
     * SessionManager and opens THIS user's database file, not whatever
     * was open (or not open) before.
     *
     * This is a deliberate architectural choice, not a workaround: Hilt
     * singletons are built once per process and aren't designed to be
     * swapped at runtime, so a full restart is the standard, honest way
     * to force a clean re-initialization against new session state --
     * the same pattern many real apps use for account switching or
     * locale changes.
     */
    private fun onAuthSuccess(username: String) {
        sessionManager.setCurrentUsername(username)

        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        }
        context.startActivity(intent)
        if (context is Activity) {
            context.finish()
        }
        Runtime.getRuntime().exit(0)
    }
}
