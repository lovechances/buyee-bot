# Explicit release summary, separate from incidents

This checkpoint restores the existing **explicit** release command; it does not add an automatic startup announcement, new channel, or new credential. Both operational incidents and release summaries continue using the existing `DISCORD_WEBHOOK_STATUS` configuration, but their event state and delivery policies remain separate. A variable name does not establish destination: verify that the configured webhook belongs to the intended `#updates` channel before invoking the send command.

After the final deployment and basic natural-acquisition validation:

```sh
railway ssh -- python -m buyeeye status-event ultra-refinement-v11 --status
railway ssh -- python -m buyeeye status-event ultra-refinement-v11
railway ssh -- python -m buyeeye status-event ultra-refinement-v11 --status
```

The `--status` form is read-only, including on an absent or legacy database. It shows the exact registered message, state, attempts, timing, and any confirmed message/channel IDs without exposing the webhook. The send form is the explicit release authorization event; ordinary daemon runs never invoke it.

Current registered copy:

> BuyEye now separates listing prices from estimated costs and handles misleading product descriptions more carefully. Inspection has also been adjusted so overlooked candidates can get a closer look.

Confirm this text still describes the final shipped behavior before sending. It makes no claim that long-term sourcing quality is proven.

## Persistence and delivery evidence

One nullable JSON field on the existing incident-state row stores release-specific records. The message and attempt claim commit **before** network I/O. A successful response must supply a message ID and channel ID; then both those IDs and the legacy release-key dedupe marker are committed. Existing legacy keys still suppress resend.

The isolated release transport performs one POST per explicit attempt, with `wait=true` and disabled mentions. Discord documents that this mode waits for send confirmation and returns the created message rather than relying on an empty acknowledgement. [Discord webhook reference](https://docs.discord.com/developers/resources/webhook#execute-webhook)

- Definite non-delivery, such as a refused connection or non-success client/rate-limit response, stays pending. Explicit retries have a cooldown and a maximum of three attempted POSTs per release event.
- Missing webhook configuration stays pending without consuming a POST attempt.
- A response timeout, server error, accepted response without message evidence, or process crash after its claim is **ambiguous**, not proof of non-delivery. Subsequent invocations refuse blind resend; inspect the destination and reconcile manually.
- The transport is bounded to 15 seconds plus child termination time. No indefinite background retry or daemon restart resend is introduced.

This is not an exactly-once-delivery guarantee: the remote service can accept a message before the local acknowledgement or durable success write becomes unavailable. In that case, preserving an unresolved event is safer than silently duplicating the release summary.

No real release summary was sent during implementation or unit tests. The final deployment report must record the actual command result and confirmed message evidence, or name the specific pending/ambiguous blocker.
