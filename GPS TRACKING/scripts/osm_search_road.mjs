async function run() {
  const url = "https://nominatim.openstreetmap.org/search?q=Polytechnic+Campus+Road,+Ahmedabad&format=json&polygon_geojson=1";
  
  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent": "RathYatraGPSTrackerTest/1.0"
      }
    });
    if (!res.ok) throw new Error(`HTTP Error ${res.status}`);
    const data = await res.json();
    console.log("OSM Nominatim Search Results:");
    console.log(JSON.stringify(data, null, 2));
  } catch (e) {
    console.error("Search query failed:", e);
  }
}

run();
