import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";

export default function HITLBadge() {
  const navigate = useNavigate();
  const [count, setCount] = useState(0);

  useEffect(() => {
    const fetchCount = async () => {
      try {
        const { data } = await api.get("/approvals", { params: { status: "pending", per_page: 1 } });
        const total = data?.total ?? (Array.isArray(data) ? data.length : 0);
        setCount(total);
      } catch {
        // Silently fail — badge just won't show
      }
    };
    fetchCount();
    const interval = setInterval(fetchCount, 30_000);
    return () => clearInterval(interval);
  }, []);

  if (count === 0) return null;
  return (
    <button onClick={() => navigate("/dashboard/approvals")}
      className="relative inline-flex items-center px-3 py-1 rounded-full bg-amber-100 text-amber-800 text-sm font-semibold animate-pulse"
      aria-label={`${count} pending approvals`}>{count} pending</button>
  );
}
