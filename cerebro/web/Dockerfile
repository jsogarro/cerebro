# Stage 1: Build the React application
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS builder

WORKDIR /app

# Copy package files and install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy source code and build
COPY . .

# VITE_API_URL can be passed as an ARG during build
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

# Stage 2: Serve the application via Nginx as a non-root user.
FROM nginx:alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de

# Remove default static assets, redirect the master PID to a path the
# `nginx` user can write to, and grant ownership of the directories nginx
# touches at runtime. Running as non-root contains the blast radius of any
# container escape and satisfies the Kubernetes Pod Security Standards
# "Restricted" profile.
RUN rm -rf /usr/share/nginx/html/* \
    && sed -i 's|^pid .*;|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf \
    && chown -R nginx:nginx /var/cache/nginx /usr/share/nginx/html

# Copy the build output and the SPA-routing nginx config. The server block
# inside `nginx.conf` listens on 8080 (non-privileged) so the nginx process
# can bind without root.
COPY --from=builder --chown=nginx:nginx /app/dist /usr/share/nginx/html
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf

USER nginx

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
