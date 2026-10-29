```summary
When a buyer's checkout is ready, the buyer gets a link to your own payment page and pays you there.
AgenticOrg never takes your buyers' money and never sees their card or UPI details.
Shopify stores send buyers to their own Shopify cart. Stores with a Plural account send buyers to a Plural payment page.
Plural payments become orders on the Orders page, and every administrator gets an email.
You choose the method once, in Getting paid, and can switch it off at any time.
```

## Choose where buyers pay

```figure
getting-paid-handoff | The buyer pays on your page | When the checkout is ready, the buyer gets your payment link and pays you there. AgenticOrg never takes the money.
```

| Method | What the buyer sees | Where the order appears |
| --- | --- | --- |
| My Shopify checkout | A link to your store's own cart with the items already in it. They pay in your Shopify checkout. | Your Shopify admin, as with any other Shopify order |
| My Plural account | A link to a Plural payment page for your Plural account. They pay by card, UPI or net banking. | The Orders page here, with an email to your administrators |
| Off | No payment link. The buyer finishes with you directly. | Wherever you take the order |

The buyer always authenticates the payment themselves on that page, for example with 3-D Secure or their UPI PIN. Nothing is charged in the chat.

## Set it up

```flow
Open Getting paid | In your merchant setup, open Getting paid for your merchant.
Shopify | Choose My Shopify checkout. Your Shopify store must already be connected; the page shows the store it will use.
Plural | Choose My Plural account. Enter your Plural client ID, client secret and webhook secret, choose Sandbox or Production, save, then press Test.
Webhook | Copy the webhook URL the page shows into your Plural dashboard, so payments are confirmed the moment they happen.
Save | Save the payment method. New checkouts use it at once.
```

Your Plural secrets are stored encrypted and are never shown again. To change one, enter the new value and save; leave a field blank to keep the saved secret. Use Sandbox until a test payment has gone through, then switch to Production with your live credentials.

## What the buyer sees

1. The buyer asks the assistant, the Telegram bot or another AI app for a product and opens a checkout.
2. When the checkout is ready, the assistant gives them your payment link and the total.
3. They open the link and pay on your page. Asking again gives the same link while it is valid.
4. With Plural, they return to a short page that says whether the payment went through. If it did not, they ask for a new link.

A Plural link is valid for 30 minutes. While it is open, the checkout cannot be changed; the buyer starts a new checkout to change the items.

## Your orders

Open **Commerce > Orders** to see every order buyers paid for through a Plural link, newest first: the total, the items, the buyer's contact details exactly as they gave them, the payment status and where they shopped (Telegram, an AI app, or another platform). Each order also sends an email to every active administrator of your workspace.

AgenticOrg creates an order only when Plural confirms the full amount. If Plural reports a payment that does not match the checkout, for example a different amount, no order is created and the payment is logged so you can refund it in Plural.

Orders paid in your Shopify checkout appear in your Shopify admin.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| The buyer is told the shop's payment page is not set up | Save your Plural credentials, or connect your Shopify store, then save the payment method again. |
| Test fails | Check the client ID and secret and that the environment matches them (Sandbox or Production). |
| Paid in Plural but no order here | Check the webhook URL in your Plural dashboard. Payments are also checked every minute, so the order appears shortly. |
| These items cannot be paid for in the shop's Shopify checkout | The items did not come from your Shopify store. Use My Plural account for them. |
