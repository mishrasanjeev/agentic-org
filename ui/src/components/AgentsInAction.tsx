import { useState, useEffect } from "react";

const AGENTS = [
  {
    name: "Priya", designation: "AP Processor - Mumbai", avatar: "P", color: "from-emerald-500 to-teal-600",
    domain: "Finance", specialization: "Domestic invoices < 5L",
    steps: [
      { text: "Parsing invoice PDF line items...", icon: "scan" },
      { text: "Validating GSTIN against government portal...", icon: "check" },
      { text: "3-way match: Invoice vs PO vs GRN...", icon: "match" },
      { text: "Match confirmed. Scheduling payment for day 9.", icon: "done" },
    ],
  },
  {
    name: "Arjun", designation: "Recon Agent - East", avatar: "A", color: "from-blue-500 to-indigo-600",
    domain: "Finance", specialization: "Bank reconciliation",
    steps: [
      { text: "Fetching 847 bank transactions...", icon: "scan" },
      { text: "Matching against GL entries by amount+date...", icon: "match" },
      { text: "843 matched (99.5%), 4 breaks identified...", icon: "check" },
      { text: "Breaks escalated to CFO for review.", icon: "done" },
    ],
  },
  {
    name: "Maya", designation: "Onboarding Specialist", avatar: "M", color: "from-purple-500 to-pink-600",
    domain: "HR", specialization: "New hire onboarding",
    steps: [
      { text: "Creating employee record in Darwinbox...", icon: "scan" },
      { text: "Provisioning IT access: email, Slack, GitHub...", icon: "check" },
      { text: "Scheduling Day 1 orientation with manager...", icon: "match" },
      { text: "Welcome kit sent. Onboarding complete.", icon: "done" },
    ],
  },
  {
    name: "Neha", designation: "Campaign Pilot", avatar: "N", color: "from-pink-500 to-rose-600",
    domain: "Marketing", specialization: "Multi-channel campaigns",
    steps: [
      { text: "Segmenting audience: 3 cohorts, 12.4K contacts...", icon: "scan" },
      { text: "Generating A/B variants for subject lines...", icon: "check" },
      { text: "Scheduling email + LinkedIn + Google Ads...", icon: "match" },
      { text: "Campaign launched. Monitoring CTR in real-time.", icon: "done" },
    ],
  },
  {
    name: "Dev", designation: "Support Triage Lead", avatar: "D", color: "from-orange-500 to-red-600",
    domain: "Operations", specialization: "Ticket classification",
    steps: [
      { text: "Scanning 42 new tickets from Zendesk...", icon: "scan" },
      { text: "Classifying by priority: P1=2, P2=8, P3=32...", icon: "check" },
      { text: "Routing to specialist queues by category...", icon: "match" },
      { text: "All tickets routed. Zero mis-classifications.", icon: "done" },
    ],
  },
  {
    name: "Vikram", designation: "Payroll Engine", avatar: "V", color: "from-indigo-500 to-blue-600",
    domain: "HR", specialization: "Monthly payroll processing",
    steps: [
      { text: "Computing gross pay for 847 employees...", icon: "scan" },
      { text: "Calculating PF, ESI, TDS deductions...", icon: "check" },
      { text: "Validating against Darwinbox attendance data...", icon: "match" },
      { text: "Payroll processed. Zero errors. Slips generated.", icon: "done" },
    ],
  },
];


function AgentCard({ agent, isActive }: { agent: typeof AGENTS[0]; isActive: boolean }) {
  const [currentStep, setCurrentStep] = useState(0);
  const [typing, setTyping] = useState(false);

  useEffect(() => {
    if (!isActive) { setCurrentStep(0); setTyping(false); return; }

    setCurrentStep(0);
    setTyping(true);

    const interval = setInterval(() => {
      setCurrentStep((prev) => {
        if (prev >= agent.steps.length - 1) {
          clearInterval(interval);
          setTyping(false);
          return prev;
        }
        return prev + 1;
      });
    }, 2000);

    return () => clearInterval(interval);
  }, [isActive, agent.steps.length]);

  const progress = ((currentStep + 1) / agent.steps.length) * 100;
  const isDone = currentStep === agent.steps.length - 1 && !typing;

  return (
    <div className={`rounded-2xl border transition-all duration-500 overflow-hidden ${
      isActive ? "border-slate-600 bg-slate-800/90 shadow-2xl shadow-blue-500/10 scale-[1.02]" : "border-slate-700/50 bg-slate-800/40"
    }`}>
      {/* Progress bar */}
      <div className="h-1 bg-slate-700">
        <div
          className={`h-full transition-all duration-1000 ease-out rounded-r ${isDone ? "bg-emerald-500" : "bg-gradient-to-r from-blue-500 to-violet-500"}`}
          style={{ width: isActive ? `${progress}%` : "0%" }}
        />
      </div>

      <div className="p-5">
        {/* Header */}
        <div className="flex items-center gap-3 mb-4">
          <div className={`w-10 h-10 rounded-full bg-gradient-to-br ${agent.color} flex items-center justify-center text-white font-bold text-sm`}>
            {agent.avatar}
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className="text-white font-semibold text-sm">{agent.name}</span>
              {isActive && (
                <span className={`text-xs px-1.5 py-0.5 rounded-full ${isDone ? "bg-emerald-500/20 text-emerald-400" : "bg-blue-500/20 text-blue-400"}`}>
                  {isDone ? "Done" : "Working"}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">{agent.designation}</p>
          </div>
          <span className="text-xs text-slate-600 bg-slate-700/50 px-2 py-0.5 rounded">{agent.domain}</span>
        </div>

        {/* Steps */}
        {isActive && (
          <div className="space-y-2">
            {agent.steps.map((step, i) => {
              const isCurrentStep = i === currentStep;
              const isPast = i < currentStep;
              const isFuture = i > currentStep;

              return (
                <div
                  key={i}
                  className={`flex items-start gap-2 transition-all duration-500 ${
                    isFuture ? "opacity-20" : isPast ? "opacity-60" : "opacity-100"
                  }`}
                >
                  <div className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 ${
                    isPast ? "bg-emerald-500/20" : isCurrentStep ? "bg-blue-500/20" : "bg-slate-700"
                  }`}>
                    {isPast ? (
                      <svg className="w-3 h-3 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    ) : isCurrentStep ? (
                      <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                    ) : (
                      <div className="w-1.5 h-1.5 rounded-full bg-slate-600" />
                    )}
                  </div>
                  <span className={`text-xs leading-relaxed ${
                    isCurrentStep ? "text-slate-200" : isPast ? "text-slate-400" : "text-slate-600"
                  }`}>
                    {step.text}
                    {isCurrentStep && typing && <span className="inline-block w-1 h-3 bg-blue-400 ml-1 animate-blink" />}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {!isActive && (
          <p className="text-xs text-slate-600">{agent.specialization}</p>
        )}
      </div>

      <style>{`
        @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
        .animate-blink { animation: blink 0.8s infinite; }
      `}</style>
    </div>
  );
}

export default function AgentsInAction() {
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % AGENTS.length);
    }, 9000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {AGENTS.map((agent, i) => (
          <button key={agent.name} onClick={() => setActiveIndex(i)} className="cursor-pointer text-left w-full" aria-label={`View ${agent.name}'s activity`} aria-pressed={i === activeIndex}>
            <AgentCard agent={agent} isActive={i === activeIndex} />
          </button>
        ))}
      </div>
    </div>
  );
}
