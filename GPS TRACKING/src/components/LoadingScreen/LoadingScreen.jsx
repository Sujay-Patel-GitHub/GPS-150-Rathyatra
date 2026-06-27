// src/components/LoadingScreen/LoadingScreen.jsx
// Shown while waiting for first Firebase data.

export function LoadingScreen() {
  return (
    <div className="fixed inset-0 bg-gray-950 flex flex-col items-center justify-center gap-6 z-50">
      {/* Animated logo */}
      <div className="relative">
        <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-orange-500 to-red-600 flex items-center justify-center text-4xl shadow-2xl shadow-orange-500/40">
          🛕
        </div>
        <div className="absolute -inset-2 rounded-2xl border-2 border-orange-500/30 animate-ping" />
      </div>

      <div className="text-center">
        <h1 className="text-2xl font-bold text-white">Rath Yatra Tracker</h1>
        <p className="text-white/50 text-sm mt-1">Connecting to GPS network…</p>
      </div>

      {/* Spinner */}
      <div className="w-8 h-8 border-2 border-orange-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}
