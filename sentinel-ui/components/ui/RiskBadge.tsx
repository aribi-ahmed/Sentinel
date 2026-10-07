import React from 'react';

interface RiskBadgeProps {
  level: string | null;
}

export const RiskBadge: React.FC<RiskBadgeProps> = ({ level }) => {
  const isElevated = level?.toUpperCase() === 'ELEVATED' || level?.toUpperCase() === 'HIGH';

  return (
    <div className="inline-flex items-center gap-2 border border-border bg-card px-4 py-2">
      <span className={`h-2 w-2 ${isElevated ? 'bg-accent animate-pulse' : 'bg-emerald-500'}`} />
      <span className="font-mono text-xs uppercase tracking-widest text-mutedForeground">
        Risk Assessment:
      </span>
      <span className={`font-mono text-xs uppercase tracking-wider font-bold ${isElevated ? 'text-accent' : 'text-emerald-400'}`}>
        {level || 'PENDING'}
      </span>
    </div>
  );
};