# syntax=docker/dockerfile:1
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint-web.sh
RUN chmod +x /docker-entrypoint-web.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80 443
ENTRYPOINT ["/docker-entrypoint-web.sh"]
