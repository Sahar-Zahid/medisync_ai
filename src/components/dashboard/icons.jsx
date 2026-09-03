// Minimal inline SVG icons for the dashboard sidebar. The project has no
// icon library dependency, and this step doesn't need one — a handful of
// small stroke icons in one file is simpler than adding a package.
const commonProps = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

export function DashboardIcon() {
  return (
    <svg {...commonProps}>
      <rect x="3.5" y="3.5" width="7.5" height="7.5" rx="1.5" />
      <rect x="13" y="3.5" width="7.5" height="4.5" rx="1.5" />
      <rect x="13" y="10.5" width="7.5" height="10" rx="1.5" />
      <rect x="3.5" y="13.5" width="7.5" height="7" rx="1.5" />
    </svg>
  )
}

export function ReportsIcon() {
  return (
    <svg {...commonProps}>
      <path d="M7 3.5h7l4 4V20a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z" />
      <path d="M14 3.5V8h4" />
      <path d="M8.5 12.5h7M8.5 15.5h7M8.5 18h4" />
    </svg>
  )
}

export function TrustedResultsIcon() {
  return (
    <svg {...commonProps}>
      <path d="M12 3.5 5 6v5.5c0 4.4 3 8 7 9 4-1 7-4.6 7-9V6l-7-2.5Z" />
      <path d="m9 12 2 2 4-4.5" />
    </svg>
  )
}

export function DoctorsIcon() {
  return (
    <svg {...commonProps}>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <path d="M16 5.5v4.5M18.2 7.75h-4.4" />
      <path d="M14.5 15c1 1.4 2.6 1.9 4 1.4a3.1 3.1 0 0 0 1-5.2c-1.4-1.2-3.2-.7-4 .6-.8-1.3-2.6-1.8-4-.6a3.1 3.1 0 0 0 1 5.2c.6.2 1.3.2 2 -.1Z" />
    </svg>
  )
}

export function AppointmentsIcon() {
  return (
    <svg {...commonProps}>
      <rect x="3.5" y="4.5" width="17" height="16" rx="2" />
      <path d="M3.5 9.5h17" />
      <path d="M8 3v3M16 3v3" />
      <path d="M8 13.5h2M8 17h2M14 13.5h2M14 17h2" />
    </svg>
  )
}

export function ProfileIcon() {
  return (
    <svg {...commonProps}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
    </svg>
  )
}

export function LogoutIcon() {
  return (
    <svg {...commonProps}>
      <path d="M9 4.5H6a1.5 1.5 0 0 0-1.5 1.5v12A1.5 1.5 0 0 0 6 19.5h3" />
      <path d="M15.5 15.5 20 11l-4.5-4.5" />
      <path d="M20 11H9" />
    </svg>
  )
}

export function AiSparkleIcon() {
  return (
    <svg {...commonProps}>
      <path d="M12 3.5 13.4 8.6 18.5 10l-5.1 1.4L12 16.5l-1.4-5.1L5.5 10l5.1-1.4L12 3.5Z" />
      <path d="M18.5 15.5 19 17l1.5.5-1.5.5-.5 1.5-.5-1.5L16.5 17l1.5-.5.5-1.5Z" />
    </svg>
  )
}

export function HistoryIcon() {
  return (
    <svg {...commonProps}>
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" />
      <path d="M3.5 4v3.5H7" />
      <path d="M12 8v4l3 2" />
    </svg>
  )
}

export function MenuIcon() {
  return (
    <svg {...commonProps}>
      <path d="M4 6.5h16M4 12h16M4 17.5h16" />
    </svg>
  )
}
