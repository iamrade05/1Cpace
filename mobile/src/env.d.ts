// Expo replaces these public values at bundle time. Never put secrets here.
declare const process: {
  env: {
    EXPO_PUBLIC_API_URL?: string;
    EXPO_PUBLIC_TENANT?: string;
    EXPO_PUBLIC_GYM_NAME?: string;
  };
};
