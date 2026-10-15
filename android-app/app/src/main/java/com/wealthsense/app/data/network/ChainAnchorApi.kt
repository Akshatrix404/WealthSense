package com.wealthsense.app.data.network

import retrofit2.http.Body
import retrofit2.http.POST

/**
 * Retrofit interface for the FastAPI bridge (see ../../../../../../backend/main.py).
 * The app never talks to the blockchain or holds a private key directly --
 * this is the only network surface for on-chain anchoring.
 */
interface ChainAnchorApi {
    @POST("anchor")
    suspend fun anchorTransaction(@Body request: AnchorRequest): AnchorResponse
}

/** Only the opaque commitment leaves the phone (see CommitmentSigner.kt). */
data class AnchorRequest(
    val commitment: String
)

data class AnchorResponse(
    val tx_hash: String,
    val explorer_url: String,
    val contract_tx_hash: String
)
