'use client';
import { useEffect, useRef } from 'react';
import type { Location } from '../lib/api';
import 'leaflet/dist/leaflet.css';

export default function LocationMap({ location }: { location: Location }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let cancelled = false;
    let map: import('leaflet').Map | undefined;
    import('leaflet').then(L => {
      if (cancelled || !container.current || location.source === 'google_wifi') return;
      map = L.map(container.current, { scrollWheelZoom: false }).setView([location.latitude, location.longitude], location.source === 'ip_coarse' ? 9 : 14);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(map);
      L.circleMarker([location.latitude, location.longitude], { radius: 8, color: '#2563eb', fillOpacity: 0.9 }).addTo(map);
      if (location.accuracy_m !== null && location.accuracy_m > 0) L.circle([location.latitude, location.longitude], { radius: location.accuracy_m, color: '#2563eb', weight: 1 }).addTo(map);
    });
    return () => { cancelled = true; map?.remove(); };
  }, [location.latitude, location.longitude, location.accuracy_m, location.source]);
  return <div className="map" ref={container} role="region" aria-label="Interactive last-known laptop location map" />;
}
