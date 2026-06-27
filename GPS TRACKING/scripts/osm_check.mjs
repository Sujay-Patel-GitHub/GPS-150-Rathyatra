async function run() {
  const lat = 23.02687217;
  const lng = 72.54657733;
  const url = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}`;
  
  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent": "RathYatraGPSTrackerTest/1.0"
      }
    });
    if (!res.ok) throw new Error(`HTTP Error ${res.status}`);
    const data = await res.json();
    console.log("Nominatim OSM Reverse Geocoding:");
    console.log(JSON.stringify(data, null, 2));
  } catch (e) {
    console.error("Nominatim query failed:", e);
  }
}

run();
