import { useState, useEffect, useRef, useCallback } from "react";
import api from "../lib/api";
import {
  isPushSupported,
  registerPushSubscription,
  isPushRegistered,
  unregisterPush,
} from "../lib/push";

interface Approval {
  id: string;
  title: string;
  priority: string;
  created_at: string;
}

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [pushEnabled, setPushEnabled] = useState(false);
  const [pushSupported] = useState(isPushSupported);
  const [toggling, setToggling] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Fetch pending approvals count and recent items
  const fetchApprovals = useCallback(async () => {
    try {
      const { data } = await api.get("/approvals", {
        params: { status: "pending", per_page: 5 },
      });
      setCount(data.total ?? data.items?.length ?? 0);
      setApprovals(
        (data.items || []).slice(0, 5).map((item: any) => ({
          id: item.id,
          title: item.title || "Pending Approval",
          priority: item.priority || "normal",
          created_at: item.created_at || "",
        }))
      );
    } catch {
      // Silently ignore — user may not have access
    }
  }, []);

  // Check push registration status on mount
  useEffect(() => {
    isPushRegistered().then(setPushEnabled);
  }, []);

  // Fetch approvals on mount and every 30 seconds
  useEffect(() => {
    fetchApprovals();
    const interval = setInterval(fetchApprovals, 30_000);
    return () => clearInterval(interval);
  }, [fetchApprovals]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  // Toggle push notifications
  const handleTogglePush = async () => {
    setToggling(true);
    try {
      if (pushEnabled) {
        await unregisterPush();
        setPushEnabled(false);
      } else {
        const success = await registerPushSubscription();
        setPushEnabled(success);
      }
    } finally {
      setToggling(false);
    }
  };

  const priorityColor: Record<string, string> = {
    critical: "text-red-600",
    high: "text-orange-500",
    normal: "text-gray-600",
    low: "text-gray-400",
  };

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Bell button */}
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="relative p-2 rounded-lg hover:bg-muted transition-colors"
        aria-label={`Notifications${count > 0 ? ` (${count} pending)` : ""}`}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {/* Unread badge */}
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 rounded-lg border bg-background shadow-lg z-50">
          {/* Header */}
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h3 className="text-sm font-semibold">Notifications</h3>
            {count > 0 && (
              <span className="text-xs text-muted-foreground">
                {count} pending
              </span>
            )}
          </div>

          {/* Push toggle */}
          <div className="border-b px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="text-sm">Push Notifications</span>
              {pushSupported ? (
                <button
                  onClick={handleTogglePush}
                  disabled={toggling}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    pushEnabled ? "bg-primary" : "bg-gray-300"
                  } ${toggling ? "opacity-50" : ""}`}
                  role="switch"
                  aria-checked={pushEnabled}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      pushEnabled ? "translate-x-6" : "translate-x-1"
                    }`}
                  />
                </button>
              ) : (
                <span className="text-xs text-muted-foreground">
                  Not supported
                </span>
              )}
            </div>
            {pushSupported && !pushEnabled && (
              <p className="mt-1 text-xs text-muted-foreground">
                Enable to get instant approval alerts
              </p>
            )}
            {!pushSupported && (
              <p className="mt-1 text-xs text-muted-foreground">
                Push not supported in this browser
              </p>
            )}
          </div>

          {/* Approval list */}
          <div className="max-h-64 overflow-y-auto">
            {approvals.length === 0 ? (
              <div className="px-4 py-6 text-center text-sm text-muted-foreground">
                No pending approvals
              </div>
            ) : (
              <ul>
                {approvals.map((item) => (
                  <li key={item.id}>
                    <a
                      href="/dashboard/approvals"
                      className="flex items-start gap-3 px-4 py-3 hover:bg-muted transition-colors"
                    >
                      <span className="mt-0.5 flex h-2 w-2 shrink-0 rounded-full bg-amber-400" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">
                          {item.title}
                        </p>
                        <p
                          className={`text-xs ${
                            priorityColor[item.priority] || "text-gray-500"
                          }`}
                        >
                          {item.priority}
                          {item.created_at && (
                            <>
                              {" "}
                              &middot;{" "}
                              {new Date(item.created_at).toLocaleDateString()}
                            </>
                          )}
                        </p>
                      </div>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Footer */}
          <div className="border-t px-4 py-2">
            <a
              href="/dashboard/approvals"
              className="block text-center text-sm font-medium text-primary hover:underline"
            >
              View all
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
