import api from "./api";

/**
 * Convert a base64url-encoded VAPID public key to a Uint8Array
 * suitable for PushManager.subscribe().
 */
export function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

/**
 * Check whether the browser supports push notifications.
 */
export function isPushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window;
}

/**
 * Check whether the service worker is registered and has an active
 * push subscription.
 */
export async function isPushRegistered(): Promise<boolean> {
  if (!isPushSupported()) return false;

  try {
    const registration = await navigator.serviceWorker.getRegistration("/sw.js");
    if (!registration) return false;
    const subscription = await registration.pushManager.getSubscription();
    return subscription !== null;
  } catch {
    return false;
  }
}

/**
 * Register the service worker, request notification permission,
 * subscribe to push, and send the subscription to the backend.
 *
 * @returns true if subscription succeeded, false otherwise.
 */
export async function registerPushSubscription(): Promise<boolean> {
  if (!isPushSupported()) {
    console.warn("[push] Push notifications not supported in this browser");
    return false;
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    console.warn("[push] Notification permission denied");
    return false;
  }

  try {
    const registration = await navigator.serviceWorker.register("/sw.js");

    // Wait for the service worker to be ready
    await navigator.serviceWorker.ready;

    // Fetch the VAPID public key from the backend
    const { data } = await api.get("/push/vapid-key");

    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(data.public_key) as BufferSource,
    });

    // Send subscription to backend
    await api.post("/push/subscribe", { subscription: subscription.toJSON() });

    console.info("[push] Push subscription registered successfully");
    return true;
  } catch (err) {
    console.error("[push] Failed to register push subscription:", err);
    return false;
  }
}

/**
 * Unsubscribe from push notifications and notify the backend.
 */
export async function unregisterPush(): Promise<void> {
  if (!isPushSupported()) return;

  try {
    const registration = await navigator.serviceWorker.getRegistration("/sw.js");
    if (!registration) return;

    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return;

    const endpoint = subscription.endpoint;

    // Unsubscribe from the browser
    await subscription.unsubscribe();

    // Notify the backend
    await api.post("/push/unsubscribe", { endpoint });

    console.info("[push] Push subscription removed");
  } catch (err) {
    console.error("[push] Failed to unregister push:", err);
  }
}
