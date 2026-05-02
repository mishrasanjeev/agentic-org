// AgenticOrg Push Notification Service Worker

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function safeNotificationUrl(value) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) {
    return "/dashboard/approvals";
  }
  return value;
}

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: "AgenticOrg", body: event.data.text() };
  }

  const hitlId = payload.data?.hitl_id || "";
  const options = {
    body: payload.body || "You have a new notification",
    icon: "/favicon-192x192.png",
    badge: "/favicon-32x32.png",
    data: {
      hitl_id: hitlId,
      url: safeNotificationUrl(payload.data?.url),
    },
    actions: [
      { action: "approve", title: "Approve" },
      { action: "reject", title: "Reject" },
    ],
    tag: "hitl-" + hitlId,
    requireInteraction: true,
  };

  event.waitUntil(
    self.registration.showNotification(payload.title || "AgenticOrg", options)
  );
});

self.addEventListener("notificationclick", (event) => {
  const notification = event.notification;
  const hitlId = notification.data?.hitl_id;
  const url = safeNotificationUrl(notification.data?.url);
  const action = event.action;

  notification.close();

  if (action === "approve" || action === "reject") {
    event.waitUntil(
      self.clients.matchAll({ type: "window" }).then((clients) => {
        const approvalUrl = hitlId
          ? "/dashboard/approvals?hitl=" + encodeURIComponent(hitlId)
          : url;
        for (const client of clients) {
          if (client.url.includes("/dashboard/approvals") && "focus" in client) {
            return client.focus();
          }
        }
        return self.clients.openWindow(approvalUrl);
      })
    );
    return;
  }

  event.waitUntil(
    self.clients.matchAll({ type: "window" }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(url) && "focus" in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
