import { useNavigate } from "react-router-dom";

export default function HITLBadge({ count }: { count: number }) {
  const navigate = useNavigate();
  if (count === 0) return null;
  return (
    <button onClick={() => navigate("/approvals")}
      className="relative inline-flex items-center px-3 py-1 rounded-full bg-amber-100 text-amber-800 text-sm font-semibold animate-pulse"
      aria-label={`${count} pending approvals`}>{count} pending</button>
  );
}
