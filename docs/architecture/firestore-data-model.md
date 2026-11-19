# Modelo inicial de Firestore

## Colecciones

### `users/{userId}`

Datos mínimos del usuario y preferencias de la tienda.

```text
displayName: string
storeName: string
role: "owner" | "staff"
createdAt: timestamp
updatedAt: timestamp
```

### `products/{productId}`

Catálogo congelado inicial de 10 a 15 productos.

```text
name: string
barcode: string | null
category: string
unit: "piece" | "package"
active: boolean
createdAt: timestamp
updatedAt: timestamp
```

### `deliveries/{deliveryId}`

Una recepción de mercancía.

```text
storeId: string
providerName: string
purchaseCost: number
paymentMethod: "cash" | "transfer" | "card" | "credit" | "other"
items: array
status: "draft" | "review" | "confirmed"
sourceImageUrl: string | null
createdBy: string
createdAt: timestamp
updatedAt: timestamp
```

Cada elemento de `items` contiene:

```text
productId: string
quantity: number
unitPurchaseCost: number
suggestedSalePrice: number
unitMargin: number
marginPercent: number
```

### `inventory/{productId}`

Existencia actual por producto.

```text
quantity: number
averagePurchaseCost: number
lastDeliveryId: string
updatedAt: timestamp
```

## Cálculos

El margen y la utilidad deben calcularse en código determinista:

- Costo por pieza = costo de compra / cantidad.
- Utilidad por pieza = precio de venta - costo por pieza.
- Margen porcentual = utilidad por pieza / precio de venta * 100.
- Nueva existencia = existencia anterior + cantidad confirmada.

Las recomendaciones de precio mostradas por el asistente deben marcarse como
estimaciones y no como determinaciones oficiales.
