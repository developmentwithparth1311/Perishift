FROM node:22-alpine
WORKDIR /app
ENV VITE_API_PROXY_TARGET=http://api:8000
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
