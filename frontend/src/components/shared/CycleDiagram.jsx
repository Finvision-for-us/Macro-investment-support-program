const PHASES = [
  { phase: 1, name: '바닥',     color: '#64748b', activeColor: '#475569' },
  { phase: 2, name: '초반 강세', color: '#10b981', activeColor: '#059669' },
  { phase: 3, name: '중반 강세', color: '#22c55e', activeColor: '#16a34a' },
  { phase: 4, name: '후반 강세', color: '#f59e0b', activeColor: '#d97706' },
  { phase: 5, name: '꼭대기',   color: '#f97316', activeColor: '#ea580c' },
  { phase: 6, name: '초반 약세', color: '#ef4444', activeColor: '#dc2626' },
  { phase: 7, name: '중반 약세', color: '#e11d48', activeColor: '#be123c' },
  { phase: 8, name: '후반 약세', color: '#9f1239', activeColor: '#881337' },
]

function polarToCartesian(cx, cy, r, angleDeg) {
  const rad = ((angleDeg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

function describeArc(cx, cy, r, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, r, endAngle)
  const end = polarToCartesian(cx, cy, r, startAngle)
  const largeArc = endAngle - startAngle > 180 ? 1 : 0
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 0 ${end.x} ${end.y}`
}

export default function CycleDiagram({ currentPhase, confidence, phaseScores }) {
  const cx = 150
  const cy = 150
  const outerR = 130
  const innerR = 80
  const segmentAngle = 360 / 8
  const gap = 2

  return (
    <div className="flex flex-col items-center">
      <svg width="300" height="300" viewBox="0 0 300 300">
        {PHASES.map((p, i) => {
          const startAngle = i * segmentAngle + gap / 2
          const endAngle = (i + 1) * segmentAngle - gap / 2
          const isActive = p.phase === currentPhase
          const midAngle = (startAngle + endAngle) / 2

          // 도넛 세그먼트
          const outerStart = polarToCartesian(cx, cy, outerR, endAngle)
          const outerEnd = polarToCartesian(cx, cy, outerR, startAngle)
          const innerStart = polarToCartesian(cx, cy, innerR, startAngle)
          const innerEnd = polarToCartesian(cx, cy, innerR, endAngle)
          const largeArc = endAngle - startAngle > 180 ? 1 : 0

          const d = [
            `M ${outerStart.x} ${outerStart.y}`,
            `A ${outerR} ${outerR} 0 ${largeArc} 0 ${outerEnd.x} ${outerEnd.y}`,
            `L ${innerStart.x} ${innerStart.y}`,
            `A ${innerR} ${innerR} 0 ${largeArc} 1 ${innerEnd.x} ${innerEnd.y}`,
            'Z',
          ].join(' ')

          // 라벨 위치
          const labelR = outerR + 16
          const labelPos = polarToCartesian(cx, cy, labelR, midAngle)

          // 점수 표시
          const scoreEntry = phaseScores?.find(s => s.phase === p.phase)
          const score = scoreEntry ? scoreEntry.score : 0

          return (
            <g key={p.phase}>
              <path
                d={d}
                fill={isActive ? p.activeColor : p.color}
                opacity={isActive ? 1 : 0.35}
                stroke={isActive ? '#1e293b' : 'none'}
                strokeWidth={isActive ? 2.5 : 0}
              />
              {isActive && (
                <path
                  d={d}
                  fill="none"
                  stroke="white"
                  strokeWidth={1}
                  opacity={0.5}
                />
              )}
              <text
                x={labelPos.x}
                y={labelPos.y}
                textAnchor="middle"
                dominantBaseline="central"
                className={`text-[9px] ${isActive ? 'font-bold fill-slate-800' : 'fill-slate-500'}`}
              >
                {p.name}
              </text>
              {score > 0 && (
                <text
                  x={labelPos.x}
                  y={labelPos.y + 12}
                  textAnchor="middle"
                  dominantBaseline="central"
                  className="text-[8px] fill-slate-400"
                >
                  {(score * 100).toFixed(0)}%
                </text>
              )}
            </g>
          )
        })}

        {/* 중앙 텍스트 */}
        <text x={cx} y={cy - 12} textAnchor="middle" className="text-sm font-bold fill-slate-800">
          {PHASES.find(p => p.phase === currentPhase)?.name || '-'}
        </text>
        <text x={cx} y={cy + 8} textAnchor="middle" className="text-xs fill-slate-500">
          Phase {currentPhase}/8
        </text>
        <text x={cx} y={cy + 24} textAnchor="middle" className="text-[10px] fill-indigo-500 font-medium">
          {confidence != null ? `${Math.round(confidence * 100)}%` : '-'}
        </text>
      </svg>
    </div>
  )
}
