// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState, useCallback } from "react";

interface GeolocationState {
  latitude: number | null;
  longitude: number | null;
  error: string | null;
  loading: boolean;
}

export function useGeolocation() {
  const [state, setState] = useState<GeolocationState>({
    latitude: null,
    longitude: null,
    error: null,
    loading: false,
  });

  const requestLocation = useCallback((): Promise<{ latitude: number; longitude: number } | { error: string }> => {
    if (!navigator.geolocation) {
      setState((s) => ({ ...s, error: "Geolocation is not supported by your browser." }));
      return Promise.resolve({ error: "Your browser doesn't support location services. You can type your borough or neighborhood instead." });
    }

    setState((s) => ({ ...s, loading: true, error: null }));

    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const coords = {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
          };
          setState({
            ...coords,
            error: null,
            loading: false,
          });
          resolve(coords);
        },
        (err) => {
          let message: string;
          switch (err.code) {
            case err.PERMISSION_DENIED:
              message = "Location access was denied. You can type your borough or neighborhood instead.";
              break;
            case err.POSITION_UNAVAILABLE:
              message = "Your device couldn't determine your location. You can type your borough or neighborhood instead.";
              break;
            case err.TIMEOUT:
              message = "The location request timed out. You can type your borough or neighborhood instead.";
              break;
            default:
              message = "I wasn't able to get your location. You can type your borough or neighborhood instead.";
          }
          setState({ latitude: null, longitude: null, error: message, loading: false });
          resolve({ error: message });
        },
        // Timeout calibration: 7s. Long enough for a healthy
        // first-fix via WiFi triangulation on macOS / Windows
        // (typically 2-5s when nearby networks are recognized),
        // short enough that the fallback (borough quick-replies)
        // appears before the user gives up. Subsequent calls within
        // 5 minutes hit the maximumAge cache and return instantly,
        // so this timeout only affects cold first requests.
        { enableHighAccuracy: false, timeout: 7_000, maximumAge: 300_000 },
      );
    });
  }, []);

  return {
    ...state,
    hasCoords: state.latitude !== null && state.longitude !== null,
    requestLocation,
  };
}
