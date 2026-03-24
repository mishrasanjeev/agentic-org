import { useState, useEffect } from "react";

const STAGES = [
  { id: "input", label: "Task Arrives", icon: "M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z", color: "from-slate-500 to-slate-600", detail: "Invoice INV-2024-4521 received from vendor" },
  { id: "agent", label: "Agent Picks Up", icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z", color: "from-blue-500 to-indigo-600", detail: "Priya (AP Processor) assigned via smart routing" },
  { id: "reason", label: "LLM Reasoning", icon: "M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z", color: "from-violet-500 to-purple-600", detail: "Gemini 2.5 Flash: OCR extract, GSTIN validate, 3-way match" },
  { id: "result", label: "Result", icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z", color: "from-emerald-500 to-teal-600", detail: "Matched with 94% confidence, payment scheduled" },
  { id: "hitl", label: "HITL Check", icon: "M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z", color: "from-amber-500 to-orange-600", detail: "Below threshold: auto-approved. Above: CFO reviews." },
];

export default function WorkflowAnimation() {
  const [activeStage, setActiveStage] = useState(0);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    // Reset progress then animate to 100 via CSS transition
    setProgress(0);
    const startTimer = setTimeout(() => setProgress(100), 50);

    const stageInterval = setInterval(() => {
      setActiveStage((prev) => (prev + 1) % STAGES.length);
    }, 3000);

    return () => {
      clearTimeout(startTimer);
      clearInterval(stageInterval);
    };
  }, [activeStage]);

  return (
    <div className="max-w-4xl mx-auto">
      {/* Desktop: horizontal flow */}
      <div className="hidden md:block">
        <div className="flex items-start gap-2">
          {STAGES.map((stage, i) => {
            const isActive = i === activeStage;
            const isPast = i < activeStage;

            return (
              <div key={stage.id} className="flex-1 flex flex-col items-center">
                {/* Node */}
                <div
                  className={`relative w-14 h-14 rounded-2xl flex items-center justify-center transition-all duration-500 ${
                    isActive
                      ? `bg-gradient-to-br ${stage.color} shadow-lg scale-110`
                      : isPast
                        ? "bg-slate-700 scale-100"
                        : "bg-slate-800 scale-90 opacity-50"
                  }`}
                >
                  <svg className={`w-6 h-6 ${isActive || isPast ? "text-white" : "text-slate-500"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={stage.icon} />
                  </svg>

                  {/* Pulse ring */}
                  {isActive && (
                    <div className="absolute inset-0 rounded-2xl border-2 border-white/30 animate-ping" style={{ animationDuration: "1.5s" }} />
                  )}
                </div>

                {/* Label */}
                <p className={`mt-3 text-xs font-semibold text-center transition-colors duration-300 ${
                  isActive ? "text-white" : isPast ? "text-slate-400" : "text-slate-600"
                }`}>
                  {stage.label}
                </p>

                {/* Detail */}
                <p className={`mt-1 text-xs text-center max-w-[140px] transition-all duration-500 ${
                  isActive ? "text-slate-300 opacity-100" : "text-slate-600 opacity-0 h-0"
                }`}>
                  {stage.detail}
                </p>

                {/* Connector line (not on last item) */}
                {i < STAGES.length - 1 && (
                  <div className="absolute" style={{ display: "none" }} />
                )}
              </div>
            );
          })}
        </div>

        {/* Connector lines between nodes */}
        <div className="flex items-center mt-[-52px] mb-8 px-[7%]">
          {STAGES.slice(0, -1).map((_, i) => {
            const isPast = i < activeStage;
            const isActive = i === activeStage;
            return (
              <div key={i} className="flex-1 h-0.5 mx-2 bg-slate-700 rounded-full overflow-hidden relative" style={{ marginTop: "0px" }}>
                <div
                  className={`h-full rounded-full ${isPast ? "bg-emerald-500 w-full transition-none" : isActive ? "bg-gradient-to-r from-blue-500 to-violet-500 transition-all duration-[2800ms] ease-linear" : "w-0 transition-none"}`}
                  style={{ width: isActive ? `${progress}%` : isPast ? "100%" : "0%" }}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* Mobile: vertical flow */}
      <div className="md:hidden space-y-3">
        {STAGES.map((stage, i) => {
          const isActive = i === activeStage;
          const isPast = i < activeStage;

          return (
            <div
              key={stage.id}
              className={`flex items-center gap-4 p-3 rounded-xl transition-all duration-500 ${
                isActive ? "bg-slate-800 border border-slate-600" : "opacity-50"
              }`}
            >
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${
                isActive ? `bg-gradient-to-br ${stage.color}` : isPast ? "bg-slate-700" : "bg-slate-800"
              }`}>
                <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={stage.icon} />
                </svg>
              </div>
              <div>
                <p className={`text-sm font-semibold ${isActive ? "text-white" : "text-slate-500"}`}>{stage.label}</p>
                {isActive && <p className="text-xs text-slate-400 mt-0.5">{stage.detail}</p>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
