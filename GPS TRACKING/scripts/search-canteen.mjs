async function run() {
  const queries = [
    "canteen Government Polytechnic Ahmedabad",
    "canteen GP Ahmedabad",
    "Government Polytechnic Ahmedabad canteen",
    "Government Polytechnic Ahmedabad"
  ];
  
  for (const q of queries) {
    const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(q)}&format=json&addressdetails=1`;
    try {
      console.log(`Searching for: ${q}`);
      const res = await fetch(url, {
        headers: {
          "User-Agent": "RathYatraGPSTrackerTest/1.0"
        }
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.length > 0) {
          console.log(`Found ${data.length} results:`);
          data.forEach(item => {
            console.log(`- ${item.display_name}`);
            console.log(`  Lat: ${item.lat}, Lng: ${item.lon}`);
            console.log(`  Type: ${item.type}, Class: ${item.class}`);
          });
          break;
        } else {
          console.log(`No results found.`);
        }
      } else {
        console.log(`HTTP Error: ${res.status}`);
      }
    } catch (e) {
      console.error(`Error:`, e);
    }
    await new Promise(r => setTimeout(r, 1000));
  }
}

run();
