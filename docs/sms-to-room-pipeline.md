# WealthSense (WealthSense fork): SMS → Room Pipeline

**Read directly from source, unmodified fork.** Files referenced:
- `android-app/app/src/main/java/com/wealthsense/app/receiver/SmsBroadcastReceiver.kt`
- `android-app/app/src/main/java/com/wealthsense/app/data/manager/SmsTransactionProcessor.kt`
- `android-app/app/src/main/java/com/wealthsense/app/data/mapper/ParsedTransactionMapper.kt`
- `android-app/app/src/main/java/com/wealthsense/app/presentation/common/icons/CategoryMapping.kt`
- `android-app/app/src/main/java/com/wealthsense/app/domain/service/RuleEngine.kt`
- `android-app/app/src/main/java/com/wealthsense/app/data/service/LlmServiceImpl.kt`
- `android-app/app/src/main/java/com/wealthsense/app/data/repository/LlmRepository.kt`
- `android-app/parser-core/src/main/kotlin/com/ritesh/parser/core/bank/*Parser.kt`

---

## 1. Exact path: BroadcastReceiver → stored Transaction entity

```
Telephony SMS_RECEIVED_ACTION intent
        │
        ▼
SmsBroadcastReceiver.onReceive()
  - filters for SMS_RECEIVED_ACTION only
  - reassembles multi-part SMS by sender, using the earliest timestamp
  - pulls SmsTransactionProcessor out of the Hilt graph via an
    @EntryPoint (needed because a BroadcastReceiver isn't Hilt-injectable
    directly)
  - launches a coroutine per unique sender on Dispatchers.IO
        │
        ▼
SmsTransactionProcessor.processAndSaveTransaction(sender, body, timestamp)
  - BankParserFactory.getParser(sender) — picks one of ~180 hand-written
    bank-specific parsers in parser-core, matched by sender ID pattern
  - if no parser matches the sender → dropped, ProcessingResult(false)
  - parser.parse(body, sender, timestamp) — a REGEX-based parser
    extracts amount, merchant, transaction type (INCOME/EXPENSE/CREDIT/
    TRANSFER/INVESTMENT), account last-4, balance, currency, bank name
  - if regex doesn't match the SMS shape → dropped
        │
        ▼
saveParsedTransaction(parsedTransaction, smsBody)
  1. ParsedTransaction.toEntity() — maps into TransactionEntity,
     including a first-pass category (see §2)
  2. Deduplication: TransactionEntity has a transactionHash; if a
     transaction with that hash already exists (including previously
     user-deleted ones), it's skipped
  3. Merchant mapping override: if the user has previously set a custom
     category for this merchant name (MerchantMappingRepository), that
     category overwrites the first-pass one
  4. Rule engine pass (see §2): user-defined rules can block the
     transaction outright, or further modify category/subcategory/
     merchant/type/description
  5. Subscription matching: if merchant+amount matches an active
     subscription, the transaction is flagged isRecurring = true and the
     subscription's next-payment date is advanced
  6. transactionRepository.insertTransaction(finalEntity) — Room INSERT
     (a second dedup check happens at the DB layer via unique index /
     rowId == -1L)
  7. Balance ledger update: a separate AccountBalanceEntity row is
     computed and inserted (uses SMS-reported balance if present,
     otherwise derives running balance from transaction type/amount)
```

Two entry points feed the same processor: `SmsBroadcastReceiver` (real-time, for SMS arriving while the app is installed) and `SmsReaderWorker` / `OptimizedSmsReaderWorker` (WorkManager background jobs, e.g. initial inbox scan or periodic catch-up). Both call the identical `SmsTransactionProcessor`, so there is one processing path, not two.

---

## 2. How categorization actually works — precise breakdown

**There is no trained ML model anywhere in this repo.** Every categorization step is deterministic, rule-based, or a static lookup table. Specifically:

| Stage | Mechanism | Where |
|---|---|---|
| First-pass category | **Keyword lookup**: merchant name is lowercased and checked against hardcoded sets of substrings/word-boundary regexes per category (`FOOD`, `SHOPPING`, etc.) | `CategoryMapping.getCategory()` |
| Income sub-typing | **String `.contains()` checks** on merchant name for "salary", "refund", "cashback", "interest", "dividend" | `ParsedTransactionMapper.determineCategory()` |
| Merchant name normalization | Deterministic case-formatting (ALL CAPS → Title Case) | `ParsedTransactionMapper.normalizeMerchantName()` |
| Per-merchant override | **User-taught lookup table**: if the user manually recategorized a merchant before, that mapping is stored and reapplied every time | `MerchantMappingRepository` |
| Rule engine | **User-authored if/then rules**: conditions (field/operator/value — `EQUALS`, `CONTAINS`, `REGEX_MATCHES`, `GREATER_THAN`, etc.) evaluated with plain boolean AND logic; actions (`SET`, `APPEND`, `CLEAR`, `BLOCK`) mutate the transaction | `RuleEngine.kt` |
| SMS parsing itself | **Regex per bank**, ~180 hand-maintained parser classes, one per bank/sender pattern | `parser-core/.../bank/*Parser.kt` |

None of this is machine learning in the sense of a model trained on labelled data and making probabilistic predictions. It's closer to a rules engine + a curated dictionary + user-taught overrides. If your project's contribution is to *add* an actual ML classifier (e.g. an on-device model trained on your own labelled transaction data) on top of or in place of `CategoryMapping`/`RuleEngine`, that is a genuinely new capability relative to this fork — worth stating plainly in your report, since it's the accurate way to show your value-add rather than claiming the existing app already does ML categorization.

---

## 3. What the on-device LLM (MediaPipe) is actually used for

`LlmServiceImpl` is a thin wrapper around Google's MediaPipe `LlmInference` API: `initialize(modelPath)`, `generateResponse(prompt)`, `generateResponseStream(prompt)`. It does nothing but send a text prompt to a loaded on-device LLM and stream back tokens. It has no awareness of "categories," "transactions," or the Room schema — it just does prompt-in, text-out.

The layer above it, `LlmRepository`, is what gives it purpose:
- It builds a **system prompt** by pulling summarized financial context (recent transactions, monthly totals, active subscriptions) via `AiContextRepository`, and injects that as the LLM's system message.
- `updateSystemPrompt()` — called from `HomeViewModel` and both SMS worker classes after new transactions are processed — **refreshes that system-prompt context string**. This is the only place the SMS/transaction pipeline touches the LLM at all, and even here it's one-directional: transaction data flows *into* the LLM's context so the chat assistant can answer questions about it. The LLM never writes back to a transaction, never sets a category, and is never called during `SmsTransactionProcessor.processAndSaveTransaction()`.
- The actual chat conversation (`ChatViewModel`) is the only consumer of `generateResponse`/`generateResponseStream`.

**Answer to "does it touch categorization": no.** The MediaPipe LLM is exclusively the backend for the conversational finance-assistant chat feature. Categorization is fully decided before the LLM is ever invoked, by the keyword lookup + rule engine described in §2.

---

## Viva-ready one-paragraph summary

*"An incoming SMS is caught by a BroadcastReceiver, matched to one of about 180 hand-written regex parsers by sender ID, and parsed into amount/merchant/type. That gets mapped to a Room entity with a first-pass category assigned by a hardcoded keyword lookup table — not a trained model. User-defined merchant overrides and a user-authored if/then rule engine can then further adjust or block it before it's inserted into Room, alongside a computed running account balance. Separately, there's an on-device MediaPipe LLM, but its only job is powering the chat assistant — it's fed a periodically-refreshed summary of the user's transactions as context so it can answer questions conversationally. It never participates in parsing or categorizing a transaction. There is currently no machine-learning model anywhere in the categorization path."*
