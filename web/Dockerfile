# 前端构建镜像
FROM node:18-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# 构建前端，输出到 /app/dist
CMD ["npm", "run", "build"]
