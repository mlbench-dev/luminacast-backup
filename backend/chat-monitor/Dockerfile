FROM node:20-alpine

WORKDIR /app

RUN apk add --no-cache --virtual .build-deps python3 make g++

COPY package.json ./
RUN npm install --omit=dev && apk del .build-deps

COPY . .

CMD ["node", "index.js"]
