FROM node:22-alpine AS hls
WORKDIR /tmp/hls
RUN npm init -y >/dev/null && npm install --no-save hls.js@1.7.4 @fontsource-variable/vazirmatn@5.3.0

FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY web/ /usr/share/nginx/html/
COPY --from=hls /tmp/hls/node_modules/hls.js/dist/hls.min.js /usr/share/nginx/html/vendor/hls.min.js
COPY --from=hls /tmp/hls/node_modules/@fontsource-variable/vazirmatn/files/ /usr/share/nginx/html/fonts/
COPY --from=hls /tmp/hls/node_modules/@fontsource-variable/vazirmatn/LICENSE /usr/share/nginx/html/fonts/OFL.txt
EXPOSE 8080
