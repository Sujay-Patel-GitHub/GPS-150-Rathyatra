async function run() {
  const lat = 23.02687217;
  const lng = 72.54657733;
  const query = `[out:json][timeout:15];way(around:100,${lat},${lng})[highway];out geom;`;
  
  const servers = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter"
  ];

  let success = false;
  for (const server of servers) {
    try {
      console.log(`Querying OSM Overpass server: ${server}...`);
      const res = await fetch(server, {
        method: "POST",
        body: `data=${encodeURIComponent(query)}`,
        headers: { "Content-Type": "application/x-www-form-urlencoded" }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      console.log(`OSM ways found within 100m: ${data.elements?.length || 0}`);
      if (data.elements && data.elements.length > 0) {
        data.elements.forEach((way, idx) => {
          console.log(`\nWay ${idx + 1}:`);
          console.log(`  OSM ID: ${way.id}`);
          console.log(`  Name: ${way.tags.name || "Unnamed"}`);
          console.log(`  Highway: ${way.tags.highway}`);
          console.log(`  Access: ${way.tags.access || "public"}`);
          if (way.geometry) {
            console.log(`  Geometry points count: ${way.geometry.length}`);
            // Calculate distance to closest geometry point
            let minDist = Infinity;
            way.geometry.forEach(p => {
              const d = haversine(lat, lng, p.lat, p.lon);
              if (d < minDist) minDist = d;
            });
            console.log(`  Distance to closest point on this way: ${minDist.toFixed(2)} meters`);
          }
        });
        success = true;
        break;
      }
    } catch (e) {
      console.warn(`Failed querying ${server}:`, e.message);
    }
  }
  if (!success) {
    console.log("Could not fetch OSM roads from any Overpass server.");
  }
}

function haversine(lat1, lng1, lat2, lng2) {
  const R   = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a   = Math.sin(dLat/2)**2 +
    Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) * Math.sin(dLng/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

run();
