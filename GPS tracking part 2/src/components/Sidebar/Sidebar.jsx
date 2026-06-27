// src/components/Sidebar/Sidebar.jsx
// Vehicle list panel — shows all vehicles with live status badges.

import { timeAgo } from "../../utils/formatters";
import { vehicleLabels, vehicleIcons } from "../../lib/constants";
import { haversine } from "../../utils/routeSnap";

function VehicleCard({ vehicleId, data, isSelected, onSelect }) {
  const online = data?.online ?? false;
  const label = data?.display_name || vehicleLabels[vehicleId] || vehicleId;
  const icon = vehicleIcons[vehicleId] ?? "🚛";

  return (
    <button
      onClick={() => onSelect(vehicleId)}
      className={`w-full text-left p-4 rounded-xl border transition-all duration-200 cursor-pointer group relative overflow-hidden
        ${
          isSelected
            ? "bg-orange-500/10 border-orange-500/60 shadow-lg shadow-orange-500/10"
            : "bg-white/3 border-white/5 hover:bg-white/8 hover:border-white/10 hover:scale-[1.01] hover:-translate-y-0.5 active:scale-[0.99]"
        }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-2xl transition-transform duration-200 group-hover:scale-110">{icon}</span>
          <div className="min-w-0">
            <p className="font-bold text-white text-sm truncate">{label}</p>
            <p className="text-[10px] text-white/35 font-mono tracking-wider mt-0.5">{vehicleId}</p>
          </div>
        </div>

        {/* Status badge */}
        <div
          className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-extrabold uppercase tracking-wide shrink-0 border
            ${online 
              ? "bg-green-500/10 border-green-500/20 text-green-400 shadow-[0_0_8px_rgba(34,197,94,0.15)]" 
              : "bg-gray-500/10 border-gray-500/20 text-gray-400"}`}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${online ? "bg-green-400 animate-pulse shadow-[0_0_4px_#22c55e]" : "bg-gray-400"}`}
          />
          {online ? "Online" : "Offline"}
        </div>
      </div>

      {/* Stats Details Grid */}
      <div className="mt-3.5 space-y-1 text-xs border-t border-white/5 pt-2.5 opacity-85">
        <div className="flex items-center justify-between text-[11px] text-white/40 font-medium">
          <span>Last update</span>
          <span className={data?.timestamp ? "text-white/70 font-semibold" : ""}>
            {data?.timestamp ? timeAgo(data.timestamp) : "No data"}
          </span>
        </div>

        {data?.speed !== undefined && (
          <div className="flex items-center justify-between text-[11px] text-white/40 font-medium">
            <span>Speed</span>
            <span className="text-white/80 font-bold font-mono">
              {data.speed} km/h
            </span>
          </div>
        )}

        {data && (
          <div className="flex items-center justify-between text-[11px] text-white/40 font-medium">
            <span>Sats / HDOP</span>
            <span className="text-white/80 font-semibold font-mono">
              {data.satellites ?? 0}sats · {data.hdop ? Number(data.hdop).toFixed(1) : "—"}
            </span>
          </div>
        )}
      </div>
    </button>
  );
}

export function Sidebar({ 
  vehicles, 
  selectedId, 
  onSelect, 
  liveDistanceMeters = null,
  adminMode = false,
  onToggleAdminMode,
  frontBackRoutes = [],
  orderedTrucks = []
}) {
  const onlineCount = Object.values(vehicles).filter((v) => v?.online).length;

  const idsToShow = Object.keys(vehicles).sort((a, b) => {
    const numA = parseInt((a.match(/\d+/) || [0])[0], 10);
    const numB = parseInt((b.match(/\d+/) || [0])[0], 10);
    if (numA !== numB) return numA - numB;
    return a.localeCompare(b);
  });

  let frontTruck = null;
  let backTruck = null;
  let frontDistance = null;
  let backDistance = null;

  if (selectedId) {
    const selectedIdx = orderedTrucks.findIndex((t) => t.id === selectedId);
    if (selectedIdx !== -1) {
      const selected = orderedTrucks[selectedIdx];
      if (selectedIdx > 0) {
        frontTruck = orderedTrucks[selectedIdx - 1];
        const frontRoute = frontBackRoutes.find(r => r.label && r.label.startsWith("Front:"));
        if (frontRoute) {
          frontDistance = frontRoute.distanceMeters;
        } else if (typeof frontTruck.lat === "number" && typeof selected.lat === "number") {
          frontDistance = haversine(selected.lat, selected.lng, frontTruck.lat, frontTruck.lng);
        } else {
          frontDistance = (frontTruck.routeMeters || 0) - (selected.routeMeters || 0);
        }
      }
      if (selectedIdx < orderedTrucks.length - 1) {
        backTruck = orderedTrucks[selectedIdx + 1];
        const backRoute = frontBackRoutes.find(r => r.label && r.label.startsWith("Back:"));
        if (backRoute) {
          backDistance = backRoute.distanceMeters;
        } else if (typeof backTruck.lat === "number" && typeof selected.lat === "number") {
          backDistance = haversine(selected.lat, selected.lng, backTruck.lat, backTruck.lng);
        } else {
          backDistance = (selected.routeMeters || 0) - (backTruck.routeMeters || 0);
        }
      }
    }
  }

  return (
    <aside className="flex flex-col h-full bg-gray-900/85 backdrop-blur-xl border-r border-white/10 select-none">
      {/* Header */}
      <div className="px-5 py-5 border-b border-white/10 bg-black/10">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-orange-500 to-red-600 flex items-center justify-center text-base shadow-lg shadow-orange-500/20 animate-pulse">
            🛕
          </div>
          <div>
            <h1 className="text-base font-black text-transparent bg-clip-text bg-gradient-to-r from-orange-400 to-red-500 leading-tight tracking-wide">
              Rath Yatra
            </h1>
            <p className="text-[10px] text-white/40 uppercase tracking-widest font-black">GPS Tracking HUD</p>
          </div>
        </div>
        <div className="flex items-center justify-between text-[11px] font-bold mt-4 bg-white/2 p-2 rounded-lg border border-white/5">
          <span className="text-white/40">Fleet Summary</span>
          <span className="text-green-400 font-extrabold flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-ping" />
            {onlineCount}/{Object.keys(vehicles).length} Active
          </span>
        </div>
      </div>

      {/* Access Level Controller */}
      <div className="px-5 py-3 border-b border-white/10 bg-black/20 flex items-center justify-between">
        <span className="text-[10px] uppercase font-black text-white/40 tracking-wider">Access Control</span>
        <button
          onClick={() => onToggleAdminMode(!adminMode)}
          className={`px-3 py-1 rounded-full text-[10px] font-extrabold transition-all duration-200 cursor-pointer border uppercase tracking-wider
            ${adminMode 
              ? "bg-red-500/20 border-red-500/30 text-red-400 hover:bg-red-500/30 shadow-[0_0_10px_rgba(239,68,68,0.1)]" 
              : "bg-white/5 border-white/10 text-white/60 hover:bg-white/10 hover:text-white"}`}
        >
          {adminMode ? "🔓 Admin Mode" : "🔒 User Mode"}
        </button>
      </div>

      {/* Spacing Alerts Widget */}
      {selectedId && (frontTruck || backTruck) && (
        <div className="px-4 py-3 border-b border-white/10 bg-black/35 space-y-2">
          <p className="text-[10px] font-black text-white/40 uppercase tracking-widest">
            Telemetry Spacing
          </p>
          
          <div className="flex flex-col gap-2.5">
            {frontTruck ? (
              <div className={`p-3 rounded-xl border flex items-center justify-between transition-all
                ${frontDistance < 25 
                  ? 'bg-red-500/10 border-red-500/30 shadow-[0_0_12px_rgba(239,68,68,0.1)]' 
                  : frontDistance > 200 
                  ? 'bg-orange-500/10 border-orange-500/30' 
                  : 'bg-green-500/10 border-green-500/30'}`}>
                <div>
                  <p className="text-[9px] text-white/40 uppercase tracking-wider font-bold">Front Neighbor</p>
                  <p className="text-[11px] text-white/80 font-bold truncate max-w-[120px]">
                    {frontTruck.display_name || vehicleLabels[frontTruck.id] || frontTruck.id}
                  </p>
                  <p className="text-xs font-black text-white font-mono mt-0.5">
                    {Math.max(0, Math.round(frontDistance))} m
                  </p>
                </div>
                <div className={`px-2 py-0.5 rounded text-[8px] font-black uppercase tracking-wide border
                  ${frontDistance < 25 
                    ? 'border-red-500 text-red-400 bg-red-500/10 animate-pulse' 
                    : frontDistance > 200 
                    ? 'border-orange-500 text-orange-400 bg-orange-500/10' 
                    : 'border-green-500 text-green-400 bg-green-500/10'}`}>
                  {frontDistance < 25 ? 'Close!' : frontDistance > 200 ? 'Gap!' : 'Stable'}
                </div>
              </div>
            ) : (
               <div className="p-2.5 rounded-xl bg-white/3 border border-dashed border-white/10 text-center text-[10px] text-white/40 font-bold uppercase tracking-wider">
                 Leading Yatra Procession
               </div>
            )}

            {backTruck ? (
              <div className={`p-3 rounded-xl border flex items-center justify-between transition-all
                ${backDistance < 25 
                  ? 'bg-red-500/10 border-red-500/30 shadow-[0_0_12px_rgba(239,68,68,0.1)]' 
                  : backDistance > 200 
                  ? 'bg-orange-500/10 border-orange-500/30' 
                  : 'bg-green-500/10 border-green-500/30'}`}>
                <div>
                  <p className="text-[9px] text-white/40 uppercase tracking-wider font-bold">Back Neighbor</p>
                  <p className="text-[11px] text-white/80 font-bold truncate max-w-[120px]">
                    {backTruck.display_name || vehicleLabels[backTruck.id] || backTruck.id}
                  </p>
                  <p className="text-xs font-black text-white font-mono mt-0.5">
                    {Math.max(0, Math.round(backDistance))} m
                  </p>
                </div>
                <div className={`px-2 py-0.5 rounded text-[8px] font-black uppercase tracking-wide border
                  ${backDistance < 25 
                    ? 'border-red-500 text-red-400 bg-red-500/10 animate-pulse' 
                    : backDistance > 200 
                    ? 'border-orange-500 text-orange-400 bg-orange-500/10' 
                    : 'border-green-500 text-green-400 bg-green-500/10'}`}>
                  {backDistance < 25 ? 'Close!' : backDistance > 200 ? 'Gap!' : 'Stable'}
                </div>
              </div>
            ) : (
               <div className="p-2.5 rounded-xl bg-white/3 border border-dashed border-white/10 text-center text-[10px] text-white/40 font-bold uppercase tracking-wider">
                 Rear Yatra Boundary
               </div>
            )}
          </div>
        </div>
      )}

      {/* Vehicle list */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden p-3 space-y-2.5 relative scrollbar-thin">
        {idsToShow.map((id) => (
          <VehicleCard
            key={id}
            vehicleId={id}
            data={vehicles[id]}
            isSelected={selectedId === id}
            onSelect={onSelect}
          />
        ))}
      </div>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-white/10 bg-black/10 shrink-0">
        <p className="text-[10px] text-white/30 text-center uppercase font-bold tracking-widest flex items-center justify-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-ping" />
          Live Telemetry Active
        </p>
      </div>
    </aside>
  );
}
