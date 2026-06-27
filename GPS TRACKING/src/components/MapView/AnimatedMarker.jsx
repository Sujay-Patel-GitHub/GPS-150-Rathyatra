// src/components/MapView/AnimatedMarker.jsx
// Smooth marker and circle interpolation for Leaflet using requestAnimationFrame.
// Glides elements smoothly between coordinate updates instead of jumping.

import { useEffect, useState, useRef } from "react";
import { Marker, Circle } from "react-leaflet";

export function AnimatedMarker({ position, icon, children, duration = 2000, eventHandlers }) {
  const [currentPos, setCurrentPos] = useState(position);
  const prevPosRef = useRef(position);
  const targetPosRef = useRef(position);
  const animationFrameRef = useRef(null);
  const startTimeRef = useRef(null);

  useEffect(() => {
    const [lat, lng] = position;
    if (typeof lat !== "number" || typeof lng !== "number") return;

    // Check if target position has actually changed
    const targetChanged =
      position[0] !== targetPosRef.current[0] ||
      position[1] !== targetPosRef.current[1];

    if (targetChanged) {
      // Start a new interpolation animation
      prevPosRef.current = currentPos;
      targetPosRef.current = position;
      startTimeRef.current = performance.now();

      const animate = (time) => {
        const elapsed = time - startTimeRef.current;
        const progress = Math.min(elapsed / duration, 1.0);

        // Linear interpolation (lerp)
        const currentLat =
          prevPosRef.current[0] +
          (targetPosRef.current[0] - prevPosRef.current[0]) * progress;
        const currentLng =
          prevPosRef.current[1] +
          (targetPosRef.current[1] - prevPosRef.current[1]) * progress;

        setCurrentPos([currentLat, currentLng]);

        if (progress < 1.0) {
          animationFrameRef.current = requestAnimationFrame(animate);
        }
      };

      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
      animationFrameRef.current = requestAnimationFrame(animate);
    }
  }, [position, duration, currentPos]);

  // Clean up animation frame on unmount
  useEffect(() => {
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  return (
    <Marker position={currentPos} icon={icon} eventHandlers={eventHandlers}>
      {children}
    </Marker>
  );
}

export function AnimatedCircle({ position, radius, pathOptions, duration = 2000 }) {
  const [currentPos, setCurrentPos] = useState(position);
  const prevPosRef = useRef(position);
  const targetPosRef = useRef(position);
  const animationFrameRef = useRef(null);
  const startTimeRef = useRef(null);

  useEffect(() => {
    const [lat, lng] = position;
    if (typeof lat !== "number" || typeof lng !== "number") return;

    const targetChanged =
      position[0] !== targetPosRef.current[0] ||
      position[1] !== targetPosRef.current[1];

    if (targetChanged) {
      prevPosRef.current = currentPos;
      targetPosRef.current = position;
      startTimeRef.current = performance.now();

      const animate = (time) => {
        const elapsed = time - startTimeRef.current;
        const progress = Math.min(elapsed / duration, 1.0);

        const currentLat =
          prevPosRef.current[0] +
          (targetPosRef.current[0] - prevPosRef.current[0]) * progress;
        const currentLng =
          prevPosRef.current[1] +
          (targetPosRef.current[1] - prevPosRef.current[1]) * progress;

        setCurrentPos([currentLat, currentLng]);

        if (progress < 1.0) {
          animationFrameRef.current = requestAnimationFrame(animate);
        }
      };

      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
      animationFrameRef.current = requestAnimationFrame(animate);
    }
  }, [position, duration, currentPos]);

  useEffect(() => {
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  return <Circle center={currentPos} radius={radius} pathOptions={pathOptions} />;
}
