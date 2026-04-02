// AgenticOrg Push Notification Service Worker

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch (e) {
    payload = { title: "AgenticOrg", body: event.data.text() };
  }

  const hitlId = payload.data?.hitl_id || "";
  const options = {
    body: payload.body || "You have a new notification",
    icon: "/favicon-192x192.png",
    badge: "/favicon-32x32.png",
    data: {
      hitl_id: hitlId,
      url: payload.data?.url || "/dashboard/approvals",
      token: payload.data?.token || "",
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
  const token = notification.data?.token;
  const url = notification.data?.url || "/dashboard/approvals";
  const action = event.action;

  notification.close();

  if (action === "approve" || action === "reject") {
    const decision = action;
    const notes =
      decision === "approve"
        ? "Approved via push notification"
        : "Rejected via push notification";

    event.waitUntil(
      fetch("/api/v1/approvals/" + hitlId + "/decide", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: "Bearer " + token } : {}),
        },
        body: JSON.stringify({ decision: decision, notes: notes }),
      })
        .then((response) => {
          if (!response.ok) {
            // If the inline action fails, open the approvals page
            return self.clients.matchAll({ type: "window" }).then((clients) => {
              if (clients.length > 0) {
                return clients[0].focus();
              }
              return self.clients.openWindow("/dashboard/approvals");
            });
          }
        })
        .catch(() => {
          return self.clients.openWindow("/dashboard/approvals");
        })
    );
  } else {
    // Default click — open the target URL
    event.waitUntil(
      self.clients.matchAll({ type: "window" }).then((windowClients) => {
        // Focus existing window if one is already open
        for (const client of windowClients) {
          if (client.url.includes(url) && "focus" in client) {
            return client.focus();
          }
        }
        return self.clients.openWindow(url);
      })
    );
  }
});
