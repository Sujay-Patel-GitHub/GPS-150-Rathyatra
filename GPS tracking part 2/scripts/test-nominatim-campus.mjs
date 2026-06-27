async function run() {
  const points = [
    { name: "Start", lat: 23.02562, lng: 72.54529 },
    { name: "Middle", lat: 23.02640, lng: 72.54604 },
    { name: "End", lat: 23.02720, lng: 72.54682 }
  ];

  for (const pt of points) {
    const url = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${pt.lat}&lon=${pt.lng}`;
    try {
      console.log(`Querying ${pt.name} (${pt.lat}, ${pt.lng})...`);
      const res = await fetch(url, {
        headers: {
          "User-Agent": "RathYatraGPSTrackerTest/1.0"
        }
      });
      if (res.ok) {
        const data = await res.json();
        console.log(`Result:`, data.display_name);
        console.log(`Road:`, data.address?.road);
      } else {
        console.log(`Failed: HTTP ${res.status}`);
      }
    } catch (e) {
      console.log(`Error:`, e.message);
    }
    await new Promise(r => setTimeout(r, 1000)); // Sleep to respect usage policy
  }
}

run();
