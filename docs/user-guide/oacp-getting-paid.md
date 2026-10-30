```summary
When a buyer's checkout is ready, the buyer gets a link to your own payment page and pays you there.
AgenticOrg never takes your buyers' money and never sees their card or UPI details.
Shopify stores send buyers to their own Shopify cart. Stores with a Plural account send buyers to a Plural payment page.
Plural payments become orders on the Orders page, and every administrator gets an email.
You choose the method once, in the Payments step of Sell Everywhere, and can switch it off at any time.
```

## Choose where buyers pay

```figure
getting-paid-handoff | The buyer pays on your page | When the checkout is ready, the arbiter checks your shop's facts and limits, then the buyer gets your payment link and pays you there. AgenticOrg never takes the money.
```

| Method | What the buyer sees | Where the order appears |
| --- | --- | --- |
| My Shopify checkout | A link to your store's own cart with the items already in it. They pay in your Shopify checkout. | Your Shopify admin, as with any other Shopify order |
| My Plural account | A link to a Plural payment page for your Plural account. They pay by card, UPI or net banking. | The Orders page here, with an email to your administrators |
| Off | No payment link. On the AgenticOrg network the buyer is told to pay with you directly. | Wherever you take the order |

The buyer always authenticates the payment themselves on that page, for example with 3-D Secure or their UPI PIN. Nothing is charged in the chat, and no agent pays on the buyer's behalf.

## Set it up

```flow
Open Payments | On the Sell Everywhere page (/dashboard/sell-everywhere), choose your shop and go to step 5, Payments. It holds the Getting paid settings.
Shopify | Choose My Shopify checkout. Your Shopify store must already be connected in step 2; the page shows the store it will use.
Plural | Under Your Plural account, choose Sandbox or Production, enter your Plural client ID, client secret and webhook secret, and select Save Plural credentials. Then select Test, which only asks Plural for an access token.
Webhook | Copy the webhook URL the page shows into your Plural dashboard, or send it to Plural support if your account has no webhook setting, so payments are confirmed the moment they happen.
Save | Choose My Plural account or My Shopify checkout and select Save payment method. New checkouts use it at once.
```

My Plural account can be saved only once your Plural credentials, including the webhook secret, are saved. My Shopify checkout can be saved only once your Shopify store is connected.

Your Plural secrets are stored encrypted and are never shown again. To change one, enter your client ID again with the new value and save; leave a secret field blank to keep the saved one. Use Sandbox until a test payment has gone through, then switch to Production with your live credentials.

You do not need a payment method to turn on Sell everywhere, but until you save one, buyers get no payment link.

## Before a link is issued

When the buyer asks to complete a ready checkout, the arbiter decides first, and the buyer gets a link only on an allow. It checks two things:

- **Your facts are still valid.** The catalog, price, stock and policy facts Grantex signed are fresh and not revoked.
- **The order is within your limits.** The largest single order, the currency, the most items per order and the daily maximum you set in step 4. The daily maximum counts what buyers paid today (by the UTC day) and the payment links still open, whichever day they were issued, in that currency. A link that lapsed unpaid no longer counts.

No buyer mandate is asked for, because the agent holds no payment credential and the buyer pays on your page. The decision is used once and recorded in your ledger. A refusal names the rule that failed and its reason, for example `daily_amount_cap_exceeded`, and no link is issued.

## What the buyer sees

1. The buyer asks for a product in ChatGPT or another AI app, in the AgenticOrg bot on Telegram, or on another agent platform, and opens a checkout.
2. When the checkout is ready, the buyer gets your payment link and the total.
3. They open the link and pay on your page. Asking again gives the same link while it is valid.
4. With Plural, they return to a short page that says whether the payment went through. If it did not, they ask for a new link.

A Plural link is valid for 30 minutes, or until the checkout expires if that is sooner. Once a Plural link has been issued, the checkout's items cannot be changed, even after the link lapses: asking to complete it again gives the same link while it is valid, or a fresh one after it lapses, and the buyer starts a new checkout to change the items. A Shopify link stays the same for the life of the checkout.

## Your orders

Open **Orders** (`/dashboard/orders`) to see every order buyers paid for through a Plural link, newest first: the total, the items, where to deliver it, the buyer's contact details, the payment status and where they shopped (Telegram, an AI app, or another platform). Each order also sends the same details in an email to every active administrator of your workspace.

The delivery column shows how the buyer chose to receive the order: shipping with the rate and fee they chose, or store pickup with the store. For shipping it also shows the address they gave their agent at checkout, line by line. The Plural payment page does not ask for an address, so ship to the one shown here. The address and contact details appear exactly as the buyer gave them; AgenticOrg does not check or correct them.

AgenticOrg creates an order only when Plural confirms the full amount, through its signed webhook or the check AgenticOrg runs every minute. If Plural reports a payment that does not match the checkout, for example a different amount, no order is created and the payment is logged so you can refund it in Plural. A payment that is only authorised, not captured, stays pending.

Orders paid in your Shopify checkout appear in your Shopify admin, not on the Orders page.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| Connect your Shopify store first | Connect your store in step 2, then save My Shopify checkout again. |
| Save your Plural credentials first, or add your Plural webhook secret | Save the client ID, client secret and webhook secret, then save My Plural account again. |
| The buyer is told the shop's payment page is not set up | Save your Plural credentials, or connect your Shopify store, then save the payment method again. |
| The buyer is told payment links are not available here | Your platform operator has not set the public web address of the AgenticOrg service, which Plural links need. |
| The buyer is told the commerce arbiter refused | The order is over one of your limits, or a signed fact has lapsed. The message names the rule; the decision in your ledger gives the exact reason. |
| Test fails | Check the client ID and secret and that the environment matches them (Sandbox or Production). |
| Paid in Plural but no order here | Check the webhook URL in your Plural dashboard. Payments are also checked every minute, so the order appears shortly. |
| These items cannot be paid for in the shop's Shopify checkout | The items did not come from your Shopify store. Use My Plural account for them. |
