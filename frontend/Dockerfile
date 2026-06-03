# Build de la SPA (Vite) y servida por nginx, que además hace de reverse-proxy de
# /api, /auth y /ws hacia el backend (mismo origen → sin CORS y SSE/WS funcionando).
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
